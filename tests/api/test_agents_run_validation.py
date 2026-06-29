"""Phase A — agents `/run` endpoint returns structured 422 on bad input.

The frontend (`apps/web/src/pages/Agents.tsx`) needs `detail` as a dict
with `agent`, `missing_fields`, and `hint` so the "Run agent" button
can surface actionable error banners instead of a useless `[object
Object]`. Today `_validate_payload` raises `HTTPException(400, str)`
which the UI cannot parse.

Tests assert the *contract*; the behaviour is delivered by the change
to `apps/api/routers/agents.py:_validate_payload`.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.routers.agents import _validate_payload


def _detail(exc: HTTPException) -> dict[str, object]:
    """`HTTPException.detail` may be str or dict; coerce to dict for assertions."""
    detail = exc.detail
    assert isinstance(detail, dict), (
        f"detail must be a dict for structured error rendering; got {type(detail).__name__}: {detail!r}"
    )
    return detail


@pytest.mark.parametrize(
    "agent,payload,missing",
    [
        # experiment_recommender requires dataset + task_description
        ("experiment_recommender", {}, ["dataset", "task_description"]),
        (
            "experiment_recommender",
            {"dataset": "demo"},
            ["task_description"],
        ),
        # optimization_coach requires session_id
        ("optimization_coach", {}, ["session_id"]),
        # evaluation_designer requires dataset
        ("evaluation_designer", {}, ["dataset"]),
        # incident_responder requires run_id
        ("incident_responder", {}, ["run_id"]),
    ],
)
def test_missing_fields_returns_structured_422(
    agent: str, payload: dict, missing: list[str]
) -> None:
    with pytest.raises(HTTPException) as ei:
        _validate_payload(agent, payload)
    assert ei.value.status_code == 422, (
        f"Validation failures must use 422 (Unprocessable Entity); got {ei.value.status_code}"
    )
    detail = _detail(ei.value)
    assert detail.get("agent") == agent
    got_missing = detail.get("missing_fields")
    assert isinstance(got_missing, list)
    for field in missing:
        assert field in got_missing, (
            f"expected {field!r} in missing_fields, got {got_missing!r}"
        )
    hint = detail.get("hint")
    assert isinstance(hint, str) and hint, "hint must be a non-empty string"


def test_unknown_agent_returns_404_unchanged() -> None:
    """Routing-level 404 keeps its existing shape; only validation moves to 422."""
    with pytest.raises(HTTPException) as ei:
        _validate_payload("nonexistent_agent", {})
    assert ei.value.status_code == 404


def test_invalid_field_type_returns_structured_422() -> None:
    """Type errors (e.g. run_id="not-a-number") also surface in missing_fields."""
    with pytest.raises(HTTPException) as ei:
        _validate_payload("incident_responder", {"run_id": "not-a-number"})
    assert ei.value.status_code == 422
    detail = _detail(ei.value)
    assert detail.get("agent") == "incident_responder"
    got_missing = detail.get("missing_fields")
    assert isinstance(got_missing, list)
    assert "run_id" in got_missing


def test_valid_payload_does_not_raise() -> None:
    """Happy path: valid payload returns a clean dict."""
    clean = _validate_payload(
        "experiment_recommender",
        {"dataset": "demo", "task_description": "summarise"},
    )
    assert clean["dataset"] == "demo"
    assert clean["task_description"] == "summarise"
