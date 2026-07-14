"""IngestJob model — one background large-dataset upload/ingest job.

Durable, DB-backed job state (unlike the in-memory synth/research registries)
so a job survives an API restart and is tenant-scoped like ``Run``/``Export``.
The composite id surfaced in the Jobs tab is ``ingest:<id>``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class IngestStatus(str, Enum):  # noqa: UP042 — matches RunStatus/ExportStatus
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


class IngestJob(SQLModel, table=True):
    __tablename__ = "ingest_jobs"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)

    # Multi-tenancy — captured at row creation from the request's Identity.
    tenant_id: str = Field(index=True)
    user_id: str

    dataset_name: str

    status: IngestStatus = IngestStatus.QUEUED

    # Provenance / detection.
    source_filename: str | None = None
    detected_format: str | None = None  # "jsonl_*" | "csv"

    # Object-store key of the uploaded blob + bytes streamed to store.
    raw_key: str | None = None
    raw_bytes: int = 0

    # Final tallies (populated as the job runs).
    records_total: int = 0
    train_count: int = 0
    valid_count: int = 0
    canary_count: int = 0
    dropped_count: int = 0

    error_message: str | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
