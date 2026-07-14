"""Background runner for large-dataset ingest jobs.

An ``IngestJob`` row is created ``queued`` by the upload endpoint after the
raw blob is streamed to the object store. This module drives that row to a
terminal state without holding the file in memory:

  queued → processing → (succeeded | failed)

The raw blob is streamed back out of the object store, parsed record by
record (:mod:`packages.dataset_ingest.streaming`), and split straight to
disk in a **staging** directory. On success the staging directory is
atomically renamed into the caller's dataset directory (both live on the
same filesystem, so ``os.replace`` is atomic and a half-written dataset is
never visible to a listing) and the raw blob is deleted. On any failure the
staging directory is removed, the row records the error, and the raw blob is
kept for debugging.

Preconditions enforced after parsing (mirrors the synchronous path's intent
that a dataset be usable): at least ``_MIN_RECORDS`` records, and no more
than ``_MAX_DROPPED_RATIO`` of lines dropped as unparseable.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.services import db
from apps.api.services.identity import Identity
from apps.api.services.identity_paths import user_datasets_dir, user_staging_dir
from apps.api.services.storage.base import ObjectStore
from apps.api.services.storage.factory import get_object_store
from packages.dataset_ingest.streaming import (
    StreamingSplitWriter,
    iter_csv_records,
    iter_jsonl_records,
)

log = logging.getLogger(__name__)

_MIN_RECORDS = 5
_MAX_DROPPED_RATIO = 0.5


class IngestJobError(Exception):
    """A recoverable, job-scoped failure recorded on the row (not raised out)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _worker_identity(job: IngestJob) -> Identity:
    """Reconstruct the caller's Identity for path + store resolution.

    Only ``tenant_id``/``user_id`` matter for the dataset/staging paths and
    for the object-store key (which is stored verbatim on the row), so a
    fixed worker role is safe here.
    """
    return Identity(
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        role="worker",
        is_worker=True,
    )


async def _parse_to_staging(
    store: ObjectStore,
    raw_key: str,
    detected_format: str | None,
    staging: Path,
) -> tuple[dict[str, int], int]:
    """Stream the raw blob → parse → split into ``staging``. Returns
    ``(counts, dropped)`` where ``counts`` is the split writer's tally."""
    writer = StreamingSplitWriter(staging)
    dropped = 0
    stream = store.get(raw_key)
    records = (
        iter_csv_records(stream)
        if detected_format == "csv"
        else iter_jsonl_records(stream)
    )
    async for record, was_dropped in records:
        if was_dropped:
            dropped += 1
        elif record is not None:
            writer.write(record)
    return writer.finalize(), dropped


def _write_readme(
    staging: Path,
    name: str,
    detected_format: str | None,
    counts: dict[str, int],
    dropped: int,
) -> None:
    ts = _now().isoformat()
    body = (
        f"# {name}\n\n"
        f"Ingested via SLM-Forge background large-dataset upload on {ts}.\n\n"
        f"- **Detected format:** `{detected_format}`\n"
        f"- **Train rows:** {counts['train']}\n"
        f"- **Valid rows:** {counts['valid']}\n"
        f"- **Canary rows:** {counts['canary']}\n"
        f"- **Records total:** {counts['records_total']}\n"
        f"- **Dropped (unparseable) lines:** {dropped}\n"
    )
    (staging / "README.md").write_text(body, encoding="utf-8")


class _JobPlan:
    """Immutable snapshot of the fields the async body needs, captured while
    transitioning the row to ``processing`` (so the transaction is short)."""

    __slots__ = ("dataset_name", "detected_format", "identity", "raw_key")

    def __init__(
        self,
        identity: Identity,
        raw_key: str,
        dataset_name: str,
        detected_format: str | None,
    ) -> None:
        self.identity = identity
        self.raw_key = raw_key
        self.dataset_name = dataset_name
        self.detected_format = detected_format


def _mark_processing(job_id: int) -> _JobPlan | None:
    """Transition to ``processing`` and snapshot the fields the async body
    needs, or return ``None`` if the row is missing or ``raw_key`` is unset."""
    with Session(db.engine) as s:
        job = s.get(IngestJob, job_id)
        if job is None:
            log.warning("ingest job %s not found; skipping", job_id)
            return None
        job.status = IngestStatus.PROCESSING
        job.started_at = _now()
        s.add(job)
        s.commit()
        if not job.raw_key:
            _mark_failed(job_id, "ingest job has no raw object key")
            return None
        return _JobPlan(
            identity=_worker_identity(job),
            raw_key=job.raw_key,
            dataset_name=job.dataset_name,
            detected_format=job.detected_format,
        )


def _mark_succeeded(job_id: int, counts: dict[str, int], dropped: int) -> None:
    with Session(db.engine) as s:
        job = s.get(IngestJob, job_id)
        if job is None:  # pragma: no cover - row deleted mid-flight
            return
        job.status = IngestStatus.SUCCEEDED
        job.records_total = counts["records_total"]
        job.train_count = counts["train"]
        job.valid_count = counts["valid"]
        job.canary_count = counts["canary"]
        job.dropped_count = dropped
        job.completed_at = _now()
        s.add(job)
        s.commit()


def _mark_failed(job_id: int, message: str) -> None:
    with Session(db.engine) as s:
        job = s.get(IngestJob, job_id)
        if job is None:  # pragma: no cover - row deleted mid-flight
            return
        job.status = IngestStatus.FAILED
        job.error_message = message[:1000]
        job.completed_at = _now()
        s.add(job)
        s.commit()


async def _run_ingest_job(job_id: int) -> None:
    """Drive a queued ingest job to a terminal state. Never raises — all
    failures are recorded on the row and logged."""
    plan = _mark_processing(job_id)
    if plan is None:
        return
    identity = plan.identity
    raw_key = plan.raw_key
    dataset_name = plan.dataset_name
    detected_format = plan.detected_format

    store = get_object_store(identity)
    staging = user_staging_dir(identity) / f"ingest-{job_id}"
    final_dir = user_datasets_dir(identity) / dataset_name

    try:
        if final_dir.exists():
            raise IngestJobError(
                f"Dataset '{dataset_name}' already exists. Pick a different name."
            )
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        counts, dropped = await _parse_to_staging(
            store, raw_key, detected_format, staging
        )
        records_total = counts["records_total"]
        if records_total < _MIN_RECORDS:
            raise IngestJobError(
                f"Only {records_total} usable record(s); need at least "
                f"{_MIN_RECORDS} to build train/valid/canary splits."
            )
        total_lines = records_total + dropped
        dropped_ratio = dropped / total_lines if total_lines else 0.0
        if dropped_ratio > _MAX_DROPPED_RATIO:
            raise IngestJobError(
                f"{dropped_ratio:.0%} of lines were unparseable "
                f"({dropped}/{total_lines}); refusing to publish."
            )

        _write_readme(staging, dataset_name, detected_format, counts, dropped)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final_dir)
        await store.delete(raw_key)
        _mark_succeeded(job_id, counts, dropped)
        log.info("ingest job %s succeeded: %s", job_id, counts)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        _mark_failed(job_id, str(exc))
        log.warning("ingest job %s failed: %s", job_id, exc)
