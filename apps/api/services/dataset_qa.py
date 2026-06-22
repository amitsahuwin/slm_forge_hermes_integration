"""PR-4 — dataset-quality reviewer.

Calls the existing ``data_quality_review`` Hermes skill against a sample of
preview rows and parses the JSON response into structured ``QAWarning``
records the UI can render.

The skill's redact-source is included in PR-1's ``HERMES_TRACE_REDACT_SOURCES``
default, so trace rows never persist the raw sample rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from apps.api.services.qa_store import QAResult, QAWarning

log = logging.getLogger("api.dataset_qa")


_VALID_SEVERITIES = {"low", "medium", "high"}


async def analyze(sample_rows: list[dict[str, Any]]) -> QAResult:
    """Run ``data_quality_review`` on the sample. Returns a ready or
    unavailable :class:`QAResult`. Never raises — Hermes failures are
    captured by the caller (`qa_store.run_qa`)."""
    raw = await asyncio.to_thread(_invoke_skill, sample_rows)
    return _parse(raw)


def _invoke_skill(sample_rows: list[dict[str, Any]]) -> str:
    """Thin sync wrapper around ``run_skill`` so ``asyncio.to_thread`` can
    target it. Kept here for local monkeypatching in tests."""
    from packages.ratchet.hermes_bridge import run_skill

    return run_skill(
        "data_quality_review",
        {"sample_rows": sample_rows, "n_examined": len(sample_rows)},
        expect_json=True,
    )


def _parse(raw: str) -> QAResult:
    """Convert the skill's JSON output into a ``QAResult``.

    Malformed JSON or a missing ``issues`` array degrades to an
    ``unavailable`` result with the parse error stashed in ``error`` — never
    raises so the caller doesn't need a try/except.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.info("data_quality_review returned invalid JSON: %s", e)
        return QAResult(status="unavailable", error=f"JSONDecodeError: {e}")

    if not isinstance(data, dict):
        return QAResult(
            status="unavailable",
            error=f"skill returned {type(data).__name__}, expected object",
        )

    issues = data.get("issues") or []
    warnings: list[QAWarning] = []
    if isinstance(issues, list):
        for raw_issue in issues:
            if not isinstance(raw_issue, dict):
                continue
            severity = str(raw_issue.get("severity", "")).strip().lower()
            if severity not in _VALID_SEVERITIES:
                severity = "low"
            category = str(raw_issue.get("kind", "other")).strip().lower() or "other"
            message = str(raw_issue.get("description", "")).strip()
            if not message:
                continue
            warnings.append(
                QAWarning(
                    severity=severity,
                    category=category,
                    message=message,
                    affected_count=int(raw_issue.get("affected_count") or 0),
                    fix=str(raw_issue.get("fix", "")).strip(),
                )
            )

    return QAResult(
        status="ready",
        overall_health=str(data.get("overall_health", "")).strip().lower() or None,
        summary=str(data.get("summary", "")).strip() or None,
        warnings=warnings,
        ready_to_train=bool(data.get("ready_to_train", False))
        if "ready_to_train" in data
        else None,
    )
