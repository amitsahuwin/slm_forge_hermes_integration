"""Background ingest-job runner (large-dataset-upload Step 3).

Exercises ``_run_ingest_job`` end to end against a real in-memory-file DB
and a real ``LocalObjectStore`` (no mocks): seed a raw blob + a ``queued``
row, run the job, and assert the published dataset, tallies, terminal
status, and raw-object cleanup. Failure paths (too few records, mostly-bad
input, name collision) must mark the row ``failed``, remove staging, leave
no published dataset, and keep the raw blob for debugging.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.services import db as db_module
from apps.api.services import identity_paths as ip_module
from apps.api.services.identity import Identity
from apps.api.services.ingest_jobs import _run_ingest_job
from apps.api.services.storage.base import ObjectNotFound
from apps.api.services.storage.factory import get_object_store, tenant_key

_IDENTITY = Identity(
    tenant_id="acme", user_id="alice", role="admin", is_admin=True
)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Real DB on a throwaway file; point the runner's module-level engine at it.
    eng = create_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)

    # Real LocalObjectStore rooted in tmp.
    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    monkeypatch.setenv("SLM_FORGE_LOCAL_STORAGE_ROOT", str(tmp_path / "store"))
    monkeypatch.delenv("SLM_FORGE_DISK_FALLBACK", raising=False)

    # Datasets + staging under the same tmp filesystem so os.replace is atomic.
    monkeypatch.setattr(ip_module, "DATASETS_ROOT", tmp_path / "datasets")
    monkeypatch.setattr(ip_module, "INGEST_STAGING_ROOT", tmp_path / "staging")

    yield
    eng.dispose()


async def _agen(data: bytes, size: int = 64) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _seed_raw(data: bytes, *, artifact_id: str, filename: str) -> str:
    key = tenant_key(
        _IDENTITY, kind="data", artifact_id=artifact_id, filename=filename
    )
    store = get_object_store(_IDENTITY)
    await store.put(key, _agen(data), content_type="application/x-ndjson")
    return key


def _new_job(raw_key: str, *, name: str, fmt: str, raw_bytes: int) -> int:
    with Session(db_module.engine) as s:
        job = IngestJob(
            tenant_id=_IDENTITY.tenant_id,
            user_id=_IDENTITY.user_id,
            dataset_name=name,
            source_filename="corpus.jsonl",
            detected_format=fmt,
            raw_key=raw_key,
            raw_bytes=raw_bytes,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        assert job.id is not None
        return job.id


def _reload(job_id: int) -> IngestJob:
    with Session(db_module.engine) as s:
        row = s.get(IngestJob, job_id)
        assert row is not None
        return row


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if ln.strip()]


async def _raw_present(key: str) -> bool:
    store = get_object_store(_IDENTITY)
    return await store.head(key) is not None


# ─────────────────────────── happy path ───────────────────────────


@pytest.mark.asyncio
async def test_run_publishes_dataset_and_deletes_raw(env: None) -> None:
    data = b"".join(
        (b'{"prompt":"p%d","completion":"c%d"}\n' % (i, i)) for i in range(20)
    )
    raw_key = await _seed_raw(data, artifact_id="ingest-1", filename="raw.jsonl")
    job_id = _new_job(raw_key, name="mycorpus", fmt="jsonl_chat", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.SUCCEEDED
    assert job.completed_at is not None
    assert job.records_total == 20
    assert job.train_count + job.valid_count + job.canary_count == 20
    assert job.valid_count >= 4
    assert job.canary_count >= 1
    assert job.dropped_count == 0

    ds = ip_module.user_datasets_dir(_IDENTITY) / "mycorpus"
    assert (ds / "train.jsonl").exists()
    assert (ds / "valid.jsonl").exists()
    assert (ds / "canary.jsonl").exists()
    assert (ds / "README.md").exists()
    total = (
        len(_lines(ds / "train.jsonl"))
        + len(_lines(ds / "valid.jsonl"))
        + len(_lines(ds / "canary.jsonl"))
    )
    assert total == 20

    # Raw blob removed on success; staging gone.
    assert not await _raw_present(raw_key)
    assert not (ip_module.user_staging_dir(_IDENTITY) / f"ingest-{job_id}").exists()


@pytest.mark.asyncio
async def test_run_csv_input(env: None) -> None:
    rows = "\n".join(f"p{i},c{i}" for i in range(10))
    data = ("prompt,completion\n" + rows + "\n").encode()
    raw_key = await _seed_raw(data, artifact_id="ingest-2", filename="raw.csv")
    job_id = _new_job(raw_key, name="csvcorpus", fmt="csv", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.SUCCEEDED
    assert job.records_total == 10
    ds = ip_module.user_datasets_dir(_IDENTITY) / "csvcorpus"
    assert (ds / "train.jsonl").exists()


@pytest.mark.asyncio
async def test_run_csv_custom_columns_produces_trainable_text(env: None) -> None:
    # Regression for dataset `sx_ds`: a CSV whose columns are NOT a known
    # prompt/completion pair must publish MLX `{text}` records (trainable),
    # not raw column dicts (which mlx_lm.lora rejects at train time).
    header = "issue_description,fix_provided,priority_value\n"
    body = "".join(f"issue {i},fix {i},high\n" for i in range(12))
    data = (header + body).encode()
    raw_key = await _seed_raw(data, artifact_id="ingest-7", filename="raw.csv")
    job_id = _new_job(raw_key, name="sxlike", fmt="csv", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.SUCCEEDED
    assert job.records_total == 12

    ds = ip_module.user_datasets_dir(_IDENTITY) / "sxlike"
    import json

    all_lines = (
        _lines(ds / "train.jsonl")
        + _lines(ds / "valid.jsonl")
        + _lines(ds / "canary.jsonl")
    )
    parsed = [json.loads(ln) for ln in all_lines]
    assert all(set(r.keys()) == {"text"} for r in parsed)
    assert all(r["text"].startswith("issue_description: issue ") for r in parsed)


# ─────────────────────────── failure paths ───────────────────────────


@pytest.mark.asyncio
async def test_run_untrainable_jsonl_schema_fails(env: None) -> None:
    # JSONL records that parse fine but match no MLX training format (no
    # messages / prompt+completion / text) must fail the ingest job with a
    # clear, actionable error instead of publishing an untrainable dataset.
    data = b"".join(
        (b'{"issue_description":"i%d","fix":"f%d"}\n' % (i, i)) for i in range(12)
    )
    raw_key = await _seed_raw(data, artifact_id="ingest-8", filename="raw.jsonl")
    job_id = _new_job(raw_key, name="badschema", fmt="jsonl_chat", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.FAILED
    assert job.error_message
    assert "MLX" in job.error_message or "format" in job.error_message.lower()
    # No dataset published; raw kept for debugging; staging cleaned.
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "badschema").exists()
    assert await _raw_present(raw_key)
    assert not (ip_module.user_staging_dir(_IDENTITY) / f"ingest-{job_id}").exists()


@pytest.mark.asyncio
async def test_run_too_few_records_fails(env: None) -> None:
    data = b'{"a":1}\n{"a":2}\n{"a":3}\n'  # only 3 < 5
    raw_key = await _seed_raw(data, artifact_id="ingest-3", filename="raw.jsonl")
    job_id = _new_job(raw_key, name="tiny", fmt="jsonl_chat", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.FAILED
    assert job.error_message
    # No dataset published; raw kept for debugging; staging cleaned.
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "tiny").exists()
    assert await _raw_present(raw_key)
    assert not (ip_module.user_staging_dir(_IDENTITY) / f"ingest-{job_id}").exists()


@pytest.mark.asyncio
async def test_run_mostly_bad_lines_fails(env: None) -> None:
    good = b"".join((b'{"a":%d}\n' % i) for i in range(6))
    bad = b"not json\n" * 8
    raw_key = await _seed_raw(good + bad, artifact_id="ingest-4", filename="raw.jsonl")
    job_id = _new_job(raw_key, name="dirty", fmt="jsonl_chat", raw_bytes=42)

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.FAILED
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "dirty").exists()
    assert await _raw_present(raw_key)


@pytest.mark.asyncio
async def test_run_duplicate_name_fails_without_clobbering(env: None) -> None:
    existing = ip_module.user_datasets_dir(_IDENTITY) / "taken"
    existing.mkdir(parents=True)
    (existing / "train.jsonl").write_text('{"keep":true}\n')

    data = b"".join((b'{"a":%d}\n' % i) for i in range(20))
    raw_key = await _seed_raw(data, artifact_id="ingest-5", filename="raw.jsonl")
    job_id = _new_job(raw_key, name="taken", fmt="jsonl_chat", raw_bytes=len(data))

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.FAILED
    # Pre-existing dataset untouched; raw kept.
    assert (existing / "train.jsonl").read_text() == '{"keep":true}\n'
    assert await _raw_present(raw_key)


@pytest.mark.asyncio
async def test_run_missing_job_is_noop(env: None) -> None:
    # Unknown id must not raise.
    await _run_ingest_job(999_999)


@pytest.mark.asyncio
async def test_run_missing_raw_object_fails(env: None) -> None:
    key = tenant_key(
        _IDENTITY, kind="data", artifact_id="ingest-6", filename="raw.jsonl"
    )
    job_id = _new_job(key, name="ghost", fmt="jsonl_chat", raw_bytes=1)

    await _run_ingest_job(job_id)

    job = _reload(job_id)
    assert job.status == IngestStatus.FAILED
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "ghost").exists()
    with pytest.raises(ObjectNotFound):
        get_object_store(_IDENTITY).get(key)
        # get() is lazy; force evaluation
        async for _ in get_object_store(_IDENTITY).get(key):
            pass
