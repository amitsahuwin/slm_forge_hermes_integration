"""CSV → chat conversion at the ingest API boundary.

Spec: docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md. Covers the sync `/file` +
`/preview` handlers and the async large-file job: every CSV ingest is
mapped (heuristic → Hermes → 400/failed), cleaned with per-reason drop
reporting, refused when >50% of rows are garbage, and published as
`{"messages": [...]}` records only.
"""
from __future__ import annotations

import io
import json
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
from packages.dataset_ingest import csv_chat

FIXTURES = Path(__file__).parents[1] / "dataset_ingest" / "fixtures"
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


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _is_chat(record: dict) -> bool:
    msgs = record.get("messages")
    return (
        isinstance(msgs, list)
        and len(msgs) == 2
        and msgs[0]["role"] == "user"
        and msgs[1]["role"] == "assistant"
    )


# ─────────────────────────── sync /preview ───────────────────────────


@pytest.mark.asyncio
async def test_preview_csv_reports_mapping_and_drops(env: list[int]) -> None:
    # force_ollama passed explicitly: direct calls bypass FastAPI, so the
    # Form(False) default would otherwise be a truthy sentinel object.
    resp = await ingest_v2.preview_file(
        file=_upload(_fixture("corrupted_issues.csv"), "c.csv"), force_ollama=False
    )
    assert resp.conversion == "direct"
    assert resp.total_records == 8
    assert all(_is_chat(r) for r in resp.sample_records)
    assert resp.column_mapping == {
        "prompt_column": "issue_description",
        "completion_column": "fix_provided",
        "method": "heuristic",
    }
    assert resp.dropped_rows == 5
    assert resp.drop_reasons["list_repr"] == 1
    assert any("dropped" in w.lower() for w in resp.warnings)


@pytest.mark.asyncio
async def test_preview_non_csv_has_no_mapping_fields(env: list[int]) -> None:
    data = b'{"prompt":"question here","completion":"answer here"}\n' * 10
    resp = await ingest_v2.preview_file(file=_upload(data, "d.jsonl"), force_ollama=False)
    assert resp.column_mapping is None
    assert resp.dropped_rows == 0
    assert resp.drop_reasons == {}


# ─────────────────────────── sync /file ───────────────────────────


@pytest.mark.asyncio
async def test_ingest_file_csv_publishes_only_chat_records(env: list[int]) -> None:
    resp = await ingest_v2.ingest_file(
        request=_req(),
        name="issues",
        file=_upload(_fixture("corrupted_issues.csv"), "issues.csv"),
        force_ollama=False,
    )
    assert resp.train + resp.valid + resp.canary == 8

    ds = ip_module.user_datasets_dir(_IDENTITY) / "issues"
    for split in ("train", "valid", "canary"):
        for record in _read_jsonl(ds / f"{split}.jsonl"):
            assert _is_chat(record), record
    readme = (ds / "README.md").read_text()
    assert "issue_description" in readme  # mapping documented
    assert "list_repr" in readme  # drop reasons documented


@pytest.mark.asyncio
async def test_ingest_file_garbage_majority_returns_400(env: list[int]) -> None:
    with pytest.raises(HTTPException) as ei:
        await ingest_v2.ingest_file(
            request=_req(),
            name="garbage",
            file=_upload(_fixture("garbage_majority.csv"), "g.csv"),
            force_ollama=False,
        )
    assert ei.value.status_code == 400
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "garbage").exists()


@pytest.mark.asyncio
async def test_ingest_file_ambiguous_hermes_down_returns_400(
    env: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(header: list[str], samples: list[dict]) -> dict:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(csv_chat, "default_hermes_resolver", broken)
    with pytest.raises(HTTPException) as ei:
        await ingest_v2.ingest_file(
            request=_req(),
            name="ambiguous",
            file=_upload(_fixture("ambiguous_headers.csv"), "a.csv"),
            force_ollama=False,
        )
    assert ei.value.status_code == 400
    assert "ollama" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_ingest_file_ambiguous_resolved_by_hermes(
    env: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        csv_chat,
        "default_hermes_resolver",
        lambda header, samples: {"prompt_column": "col_a", "completion_column": "col_b"},
    )
    resp = await ingest_v2.ingest_file(
        request=_req(),
        name="ambig-ok",
        file=_upload(_fixture("ambiguous_headers.csv"), "a.csv"),
        force_ollama=False,
    )
    assert resp.train + resp.valid + resp.canary == 8


# ─────────────────────────── async large-file job ───────────────────────────


def _rows() -> list[IngestJob]:
    with Session(db_module.engine) as s:
        return list(s.exec(select(IngestJob)))


@pytest.mark.asyncio
async def test_large_csv_job_publishes_chat_records(env: list[int]) -> None:
    resp = await ingest_v2.ingest_file_large(
        request=_req(),
        name="bigissues",
        file=_upload(_fixture("corrupted_issues.csv"), "issues.csv"),
    )
    assert resp.detected_format == "csv"
    await _run_ingest_job(_rows()[0].id)

    job = _rows()[0]
    assert job.status == IngestStatus.SUCCEEDED, job.error_message
    ds = ip_module.user_datasets_dir(_IDENTITY) / "bigissues"
    published = []
    for split in ("train", "valid", "canary"):
        p = ds / f"{split}.jsonl"
        if p.exists():
            published.extend(_read_jsonl(p))
    assert len(published) == 8
    assert all(_is_chat(r) for r in published)
    readme = (ds / "README.md").read_text()
    assert "list_repr" in readme


@pytest.mark.asyncio
async def test_large_csv_job_garbage_majority_fails(env: list[int]) -> None:
    good = "".join(f'"real question number {i}","real answer number {i}"\n' for i in range(6))
    bad = "".join(f'"question missing its answer {i}",\n' for i in range(10))
    data = ("prompt,completion\n" + good + bad).encode()

    await ingest_v2.ingest_file_large(
        request=_req(), name="biggarbage", file=_upload(data, "g.csv")
    )
    await _run_ingest_job(_rows()[0].id)

    job = _rows()[0]
    assert job.status == IngestStatus.FAILED
    assert "unusable" in (job.error_message or "").lower()
    assert not (ip_module.user_datasets_dir(_IDENTITY) / "biggarbage").exists()


@pytest.mark.asyncio
async def test_large_csv_job_hermes_down_fails_with_message(
    env: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(header: list[str], samples: list[dict]) -> dict:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(csv_chat, "default_hermes_resolver", broken)
    await ingest_v2.ingest_file_large(
        request=_req(),
        name="ambigjob",
        file=_upload(_fixture("ambiguous_headers.csv"), "a.csv"),
    )
    await _run_ingest_job(_rows()[0].id)

    job = _rows()[0]
    assert job.status == IngestStatus.FAILED
    assert "ollama" in (job.error_message or "").lower()