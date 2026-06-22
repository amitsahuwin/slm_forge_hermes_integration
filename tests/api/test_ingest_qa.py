"""PR-4 — HTTP wiring: preview returns a ``qa_id`` and the new
``GET /api/v1/ingest/qa/{qa_id}`` endpoint goes pending → ready as the
background task completes.

We exercise the route handlers directly (TestClient isn't needed) to keep
the test focused on the QA contract.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException

from apps.api.routers.ingest import (
    QAStatusResponse,
    _build_preview,
    get_qa,
)
from apps.api.services import dataset_qa as dq
from apps.api.services import qa_store


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Each test gets a clean qa_store + a tmp staging dir (the real one is
    the Docker-only ``/app/data/.ingest_staging``)."""
    from packages.ingest import staging

    monkeypatch.setattr(staging, "STAGE_DIR", tmp_path / "_staging")
    qa_store.clear()
    monkeypatch.delenv("HERMES_QA_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_QA_TIMEOUT_S", raising=False)
    yield
    qa_store.clear()


def _seed_rows(n: int = 6) -> list[dict[str, Any]]:
    return [{"prompt": f"p{i}", "response": f"r{i}"} for i in range(n)]


def test_build_preview_returns_qa_id_and_registers_pending(monkeypatch: pytest.MonkeyPatch):
    """Preview returns a qa_id; immediate GET sees status=pending."""
    bg = BackgroundTasks()
    rows = _seed_rows()

    out = _build_preview(rows, "upload", "jsonl", background=bg)
    assert out.qa_id is not None
    # One background task scheduled (the QA scan).
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is qa_store.run_qa

    # GET endpoint reflects the pending slot.
    resp = get_qa(out.qa_id)
    assert isinstance(resp, QAStatusResponse)
    assert resp.status == "pending"
    assert resp.warnings == []


def test_build_preview_disabled_returns_no_qa_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_QA_ENABLED", "false")
    bg = BackgroundTasks()
    out = _build_preview(_seed_rows(), "upload", "jsonl", background=bg)
    assert out.qa_id is None
    assert len(bg.tasks) == 0


def test_build_preview_without_background_returns_no_qa_id():
    """Callers that don't pass a BackgroundTasks (legacy paths) get no qa_id."""
    out = _build_preview(_seed_rows(), "upload", "jsonl", background=None)
    assert out.qa_id is None


def test_build_preview_raises_400_on_empty_rows():
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as ei:
        _build_preview([], "upload", "jsonl", background=bg)
    assert ei.value.status_code == 400


def test_get_qa_404_on_unknown_id():
    with pytest.raises(HTTPException) as ei:
        get_qa("nonexistent12")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_full_lifecycle_pending_then_ready(monkeypatch: pytest.MonkeyPatch):
    """Preview → background task runs → GET flips from pending to ready."""
    bg = BackgroundTasks()
    out = _build_preview(_seed_rows(), "upload", "jsonl", background=bg)
    assert out.qa_id is not None

    # Initially: pending
    pending = get_qa(out.qa_id)
    assert pending.status == "pending"

    # Simulate the background task by running it directly (FastAPI normally
    # invokes ``bg.tasks`` after the response — equivalent here).
    monkeypatch.setattr(
        dq,
        "_invoke_skill",
        lambda rows: (
            '{"overall_health":"good","summary":"clean","issues":[],"ready_to_train":true}'
        ),
    )
    task = bg.tasks[0]
    await task.func(*task.args)

    # Now: ready
    ready = get_qa(out.qa_id)
    assert ready.status == "ready"
    assert ready.overall_health == "good"
    assert ready.ready_to_train is True
    assert ready.warnings == []


@pytest.mark.asyncio
async def test_lifecycle_marks_unavailable_when_hermes_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    import httpx

    bg = BackgroundTasks()
    out = _build_preview(_seed_rows(), "upload", "jsonl", background=bg)

    def explode(rows):
        raise httpx.ConnectError("ollama unreachable")

    monkeypatch.setattr(dq, "_invoke_skill", explode)
    task = bg.tasks[0]
    await task.func(*task.args)

    resp = get_qa(out.qa_id)  # type: ignore[arg-type]
    assert resp.status == "unavailable"
    assert resp.error is not None and "ConnectError" in resp.error


@pytest.mark.asyncio
async def test_parses_multiple_warnings_with_normalized_fields(
    monkeypatch: pytest.MonkeyPatch,
):
    bg = BackgroundTasks()
    out = _build_preview(_seed_rows(20), "upload", "jsonl", background=bg)

    fake = """
    {
      "overall_health": "poor",
      "summary": "Multiple issues.",
      "issues": [
        {"severity": "HIGH", "kind": "duplicates",
         "description": "10 duplicates", "affected_count": 10, "fix": "dedupe"},
        {"severity": "weird-value", "kind": "off_topic",
         "description": "drift from domain", "affected_count": 3},
        {"severity": "low", "kind": "",
         "description": "minor formatting"}
      ]
    }
    """
    monkeypatch.setattr(dq, "_invoke_skill", lambda rows: fake)

    task = bg.tasks[0]
    await task.func(*task.args)

    resp = get_qa(out.qa_id)  # type: ignore[arg-type]
    assert resp.status == "ready"
    assert resp.overall_health == "poor"
    sevs = [w.severity for w in resp.warnings]
    cats = [w.category for w in resp.warnings]
    # "HIGH" normalises to "high"; unknown severity defaults to "low";
    # empty kind defaults to "other".
    assert "high" in sevs
    assert "low" in sevs
    assert "other" in cats


def test_qa_warning_message_required(monkeypatch: pytest.MonkeyPatch):
    """An issue with no ``description`` is dropped, not surfaced as empty noise."""
    from apps.api.services.dataset_qa import _parse

    raw = '{"issues":[{"severity":"high","kind":"duplicates","description":""}]}'
    out = _parse(raw)
    assert out.status == "ready"
    assert out.warnings == []  # empty-description issue filtered out
