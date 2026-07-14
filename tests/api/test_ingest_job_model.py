"""Tests for the IngestJob SQLModel (large-dataset-upload Step 1)."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.ingest_job import IngestJob, IngestStatus


def _engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return eng


def test_status_enum_values() -> None:
    assert IngestStatus.QUEUED == "queued"
    assert IngestStatus.PROCESSING == "processing"
    assert IngestStatus.SUCCEEDED == "succeeded"
    assert IngestStatus.FAILED == "failed"
    assert {s.value for s in IngestStatus} == {
        "queued",
        "processing",
        "succeeded",
        "failed",
    }


def test_defaults() -> None:
    job = IngestJob(
        tenant_id="acme",
        user_id="alice",
        dataset_name="my-corpus",
    )
    assert job.status == IngestStatus.QUEUED
    assert job.raw_bytes == 0
    assert job.records_total == 0
    assert job.train_count == 0
    assert job.valid_count == 0
    assert job.canary_count == 0
    assert job.dropped_count == 0
    assert job.source_filename is None
    assert job.detected_format is None
    assert job.raw_key is None
    assert job.error_message is None
    assert isinstance(job.created_at, datetime)
    assert job.started_at is None
    assert job.completed_at is None


def test_persist_and_read_back() -> None:
    eng = _engine()
    with Session(eng) as s:
        job = IngestJob(
            tenant_id="acme",
            user_id="alice",
            dataset_name="my-corpus",
            source_filename="corpus.jsonl",
            detected_format="jsonl_chat",
            raw_key="acme/data/upload-abc/corpus.jsonl",
            raw_bytes=12345,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        assert job.id is not None

    with Session(eng) as s:
        rows = s.exec(select(IngestJob).where(IngestJob.tenant_id == "acme")).all()
        assert len(rows) == 1
        got = rows[0]
        assert got.dataset_name == "my-corpus"
        assert got.raw_bytes == 12345
        assert got.status == IngestStatus.QUEUED


def test_tenant_id_is_indexed() -> None:
    col = IngestJob.__table__.columns["tenant_id"]  # type: ignore[attr-defined]
    assert col.index is True
