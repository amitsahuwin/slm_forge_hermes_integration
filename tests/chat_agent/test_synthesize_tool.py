"""Chat-agent synthesize tool — was missing entirely, which is why the
user said "the generated new synthesized dataset is not getting saved
from chat."

When the user asks the chat agent to synthesize a dataset, the agent
needs an actual tool that POSTs ``/api/v1/synth/start`` and surfaces
the job id. Without this, the LLM hallucinates a success response and
the dataset never appears on disk; downstream experiments then fail
with "dataset not found".

These tests pin the contract for the two new tools:
  * ``synthesize_dataset`` — fires the job and returns ``{job_id, ...}``.
  * ``get_synth_job_status`` — polls a job by id.
Both are registered in ``ALL_TOOLS`` so the LangGraph agent can call them.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from packages.chat_agent import tools as chat_tools


def _mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def http_handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        if request.content:
            captured["body"] = request.content.decode()
        return handler(request)

    transport = httpx.MockTransport(http_handler)

    def fake_client() -> httpx.Client:
        return httpx.Client(
            base_url=chat_tools.API_URL,
            timeout=chat_tools._DEFAULT_TIMEOUT,
            transport=transport,
        )

    monkeypatch.setattr(chat_tools, "_client", fake_client)
    return captured


def test_synthesize_dataset_tool_is_registered() -> None:
    """``synthesize_dataset`` must be in ALL_TOOLS so the LangGraph agent
    can actually invoke it. Without registration, the LLM either ignores
    the request or hallucinates a fake success — which is the user-visible
    bug 'synthesized dataset not saving'."""
    tool_names = {getattr(t, "name", "") for t in chat_tools.ALL_TOOLS}
    assert "synthesize_dataset" in tool_names
    assert "get_synth_job_status" in tool_names


def test_synthesize_dataset_posts_to_synth_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool must POST a SynthRequest to /api/v1/synth/start (not /sessions
    or any other endpoint). The new_dataset field is REQUIRED for the dataset
    to land on disk under that name."""
    captured = _mock_transport(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={"job_id": "abc123", "source_count": 50, "target_count": 200},
        ),
    )

    out = chat_tools.synthesize_dataset.invoke(  # type: ignore[attr-defined]
        {
            "source_dataset": "medical-qa-rural-tn",
            "new_dataset": "medical-qa-expanded",
            "target_count": 200,
            "style_guidance": "Keep the rural-care tone",
        }
    )

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/synth/start")
    import json as _json

    body = _json.loads(captured["body"])
    assert body["source_dataset"] == "medical-qa-rural-tn"
    assert body["new_dataset"] == "medical-qa-expanded"
    assert body["target_count"] == 200
    assert body["style_guidance"] == "Keep the rural-care tone"
    assert out["job_id"] == "abc123"
    assert out["new_dataset"] == "medical-qa-expanded"
    assert out["status"] == "queued"


def test_synthesize_dataset_surfaces_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API returns 404 (source dataset not found) the tool must
    surface a structured ``error`` field so the UI / LLM can react —
    not raise."""
    _mock_transport(
        monkeypatch,
        lambda req: httpx.Response(
            404, json={"detail": "Source dataset 'nope' not found"}
        ),
    )
    out = chat_tools.synthesize_dataset.invoke(  # type: ignore[attr-defined]
        {
            "source_dataset": "nope",
            "new_dataset": "x",
            "target_count": 50,
        }
    )
    assert "error" in out


def test_get_synth_job_status_polls_jobs_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_transport(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "job_id": "abc123",
                "status": "completed",
                "source_dataset": "x",
                "new_dataset": "y",
                "target_count": 200,
                "generated": 200,
                "batch": 8,
                "dropped_total": 4,
                "created_at": "2026-06-24T21:00:00+00:00",
                "completed_at": "2026-06-24T21:02:00+00:00",
                "error": None,
                "result": {"path": "data/datasets/y"},
            },
        ),
    )
    out = chat_tools.get_synth_job_status.invoke({"job_id": "abc123"})  # type: ignore[attr-defined]

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/api/v1/synth/jobs/abc123")
    assert out["status"] == "completed"
    assert out["generated"] == 200
    assert out["new_dataset"] == "y"


def test_synthesize_dataset_target_count_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """target_count must be in [8, 5000]; the tool should reject obviously
    invalid values up front rather than wait for the API's 422."""
    out = chat_tools.synthesize_dataset.invoke(  # type: ignore[attr-defined]
        {"source_dataset": "x", "new_dataset": "y", "target_count": 0}
    )
    assert "error" in out
    out = chat_tools.synthesize_dataset.invoke(  # type: ignore[attr-defined]
        {"source_dataset": "x", "new_dataset": "y", "target_count": 99_999}
    )
    assert "error" in out