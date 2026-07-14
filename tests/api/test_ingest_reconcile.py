"""Restart reconciler for orphaned ingest jobs (large-dataset-upload Step 6).

Ingest jobs run as in-process asyncio tasks (Approach A). When the API
restarts, any task that was mid-flight (``processing``) or never started
(``queued``) is gone — no worker will ever pick it up, so the row would
otherwise sit non-terminal forever. ``_reconcile_orphaned_ingest_jobs``
runs at startup and drives every such row to ``failed`` with a clear
message. Already-terminal rows (``succeeded`` / ``failed``) are untouched.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.services import db as db_module
from apps.api.services.db import _reconcile_orphaned_ingest_jobs


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    eng = create_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    yield
    eng.dispose()


def _seed(status: IngestStatus, name: str) -> int:
    with Session(db_module.engine) as s:
        job = IngestJob(
            tenant_id="acme",
            user_id="alice",
            dataset_name=name,
            status=status,
            raw_key="acme/admin/alice/data/ingest-x/raw.jsonl",
            raw_bytes=10,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id or 0


def _reload(job_id: int) -> IngestJob:
    with Session(db_module.engine) as s:
        row = s.get(IngestJob, job_id)
        assert row is not None
        return row


def test_reconcile_fails_processing_and_queued(engine: None) -> None:
    proc = _seed(IngestStatus.PROCESSING, "proc")
    queued = _seed(IngestStatus.QUEUED, "queued")

    _reconcile_orphaned_ingest_jobs()

    for jid in (proc, queued):
        row = _reload(jid)
        assert row.status == IngestStatus.FAILED
        assert row.error_message == "interrupted by API restart"
        assert row.completed_at is not None


def test_reconcile_leaves_terminal_rows_untouched(engine: None) -> None:
    ok = _seed(IngestStatus.SUCCEEDED, "ok")
    bad = _seed(IngestStatus.FAILED, "bad")
    with Session(db_module.engine) as s:
        row = s.get(IngestJob, bad)
        assert row is not None
        row.error_message = "original failure"
        s.add(row)
        s.commit()

    _reconcile_orphaned_ingest_jobs()

    assert _reload(ok).status == IngestStatus.SUCCEEDED
    assert _reload(ok).error_message is None
    bad_row = _reload(bad)
    assert bad_row.status == IngestStatus.FAILED
    assert bad_row.error_message == "original failure"


def test_reconcile_noop_when_no_orphans(engine: None) -> None:
    _seed(IngestStatus.SUCCEEDED, "ok")
    _reconcile_orphaned_ingest_jobs()
    with Session(db_module.engine) as s:
        rows = list(s.exec(select(IngestJob)))
    assert len(rows) == 1
    assert rows[0].status == IngestStatus.SUCCEEDED
