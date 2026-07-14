"""Large-dataset upload endpoint (large-dataset-upload Step 4).

Exercises ``POST /api/v1/ingest/file/large`` at the handler level (no
TestClient needed): a pre-formatted JSONL/CSV file is streamed to the
object store under the tenant key, an ``IngestJob`` row is created
``queued``, and the response carries the ``ingest:<id>`` composite job id.
The background runner is stubbed out here (it has its own suite); the happy
path then drives the real runner once to confirm end-to-end publication.

Guardrails: over-cap → 413 (no row, no object), duplicate name → 409,
non-JSONL/CSV → 422.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.routers import ingest_v2
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module
from apps.api.services import identity_paths as ip_module
from apps.api.services.identity import Identity
from apps.api.services.ingest_jobs import _run_ingest_job
from apps.api.services.ingest_settings import get_ingest_settings

_IDENTITY = Identity(tenant_id="local", user_id="local-admin", role="admin", is_admin=True)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    eng = create_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)

    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    monkeypatch.setenv("SLM_FORGE_LOCAL_STORAGE_ROOT", str(tmp_path / "store"))
    monkeypatch.delenv("SLM_FORGE_DISK_FALLBACK", raising=False)
    monkeypatch.setenv("SLM_FORGE_AUTH_ENABLED", "false")
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.setattr(ip_module, "DATASETS_ROOT", tmp_path / "datasets")
    monkeypatch.setattr(ip_module, "INGEST_STAGING_ROOT", tmp_path / "staging")
    get_ingest_settings.cache_clear()

    # Capture scheduled job ids instead of spawning real background tasks.
    scheduled: list[int] = []
    monkeypatch.setattr(ingest_v2, "_schedule_ingest", scheduled.append)

    yield scheduled
    get_ingest_settings.cache_clear()
    auth_settings_module.get_auth_settings.cache_clear()
    eng.dispose()


def _req() -> object:
    class _R:
        class state:  # noqa: N801 - mimic request.state
            user = None

    return _R()


def _upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


def _rows() -> list[IngestJob]:
    with Session(db_module.engine) as s:
        return list(s.exec(select(IngestJob)))


# ─────────────────────────── happy paths ───────────────────────────


@pytest.mark.asyncio
async def test_large_jsonl_creates_queued_job(env: list[int]) -> None:
    data = b"".join(
        (b'{"prompt":"p%d","completion":"c%d"}\n' % (i, i)) for i in range(20)
    )
    resp = await ingest_v2.ingest_file_large(
        request=_req(), name="bigcorpus", file=_upload(data, "corpus.jsonl")
    )

    assert resp.job_id.startswith("ingest:")
    assert resp.name == "bigcorpus"
    assert resp.status == IngestStatus.QUEUED.value
    assert resp.detected_format.startswith("jsonl_")
    assert resp.raw_bytes == len(data)

    rows = _rows()
    assert len(rows) == 1
    job = rows[0]
    assert job.status == IngestStatus.QUEUED
    assert job.dataset_name == "bigcorpus"
    assert job.raw_key
    assert env == [job.id]  # scheduled exactly once

    # Drive the real runner to confirm end-to-end publication.
    await _run_ingest_job(job.id)
    ds = ip_module.user_datasets_dir(_IDENTITY) / "bigcorpus"
    assert (ds / "train.jsonl").exists()
    assert (ds / "valid.jsonl").exists()


@pytest.mark.asyncio
async def test_large_csv_detected(env: list[int]) -> None:
    rows = "\n".join(f"p{i},c{i}" for i in range(10))
    data = ("prompt,completion\n" + rows + "\n").encode()
    resp = await ingest_v2.ingest_file_large(
        request=_req(), name="csvbig", file=_upload(data, "corpus.csv")
    )
    assert resp.detected_format == "csv"
    assert _rows()[0].detected_format == "csv"


# ─────────────────────────── guardrails ───────────────────────────


@pytest.mark.asyncio
async def test_over_cap_returns_413_no_row_no_object(
    env: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLM_FORGE_MAX_UPLOAD_BYTES", "100")
    monkeypatch.setenv("SLM_FORGE_INGEST_SYNC_MAX_BYTES", "100")
    get_ingest_settings.cache_clear()
    data = b"".join((b'{"a":%d}\n' % i) for i in range(200))  # > 100 bytes

    with pytest.raises(HTTPException) as ei:
        await ingest_v2.ingest_file_large(
            request=_req(), name="toobig", file=_upload(data, "x.jsonl")
        )
    assert ei.value.status_code == 413
    assert _rows() == []
    assert env == []


@pytest.mark.asyncio
async def test_duplicate_name_returns_409(env: list[int]) -> None:
    (ip_module.user_datasets_dir(_IDENTITY) / "dup").mkdir(parents=True)
    data = b"".join((b'{"a":%d}\n' % i) for i in range(20))

    with pytest.raises(HTTPException) as ei:
        await ingest_v2.ingest_file_large(
            request=_req(), name="dup", file=_upload(data, "x.jsonl")
        )
    assert ei.value.status_code == 409
    assert _rows() == []


@pytest.mark.asyncio
async def test_non_jsonl_csv_returns_422(env: list[int]) -> None:
    data = b"just some prose that is not a dataset at all.\n" * 5

    with pytest.raises(HTTPException) as ei:
        await ingest_v2.ingest_file_large(
            request=_req(), name="prose", file=_upload(data, "notes.txt")
        )
    assert ei.value.status_code == 422
    assert _rows() == []
    assert env == []
