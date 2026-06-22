"""PR-1 A3 — Hermes response bodies never leak through logs or persisted traces.

Sentinel-based assertions: we put a unique token in the mocked Ollama
response and assert it never shows up in:

  * Any log record (info/debug/warning/error).
  * Persisted ``HermesTrace.response_body`` when the source is in the
    default redact list.

The same source NOT in the redact list (e.g. ``"chat"``) keeps the body
verbatim — so the redaction is targeted, not all-or-nothing.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

import packages.ratchet.hermes_bridge as hb

SENTINEL = "QWERTYUIOP_SECRET_PII_42"


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.status_code = 200
        self._json = {"message": {"content": content}}
        self.text = '{"message":{"content":"' + content + '"}}'

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None: ...


@pytest.fixture
def _ollama_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        hb.httpx,
        "post",
        lambda url, json, timeout: _StubResponse(SENTINEL),
    )


@pytest.fixture
def _capture_trace(monkeypatch: pytest.MonkeyPatch):
    """Capture what the trace recorder would persist, after redaction."""
    calls: list[dict[str, Any]] = []

    def fake_record(*, source, request_body, response_text, error, duration_ms, attempts=1, **_):
        # Mirror the production code path: re-apply the same redaction the real
        # recorder runs before persistence. This lets us pin exactly what would
        # have hit the DB.
        import json as _json

        request_str = hb._maybe_redact_body(source, _json.dumps(request_body, ensure_ascii=False))
        response_str = hb._maybe_redact_body(source, response_text or "")
        calls.append(
            {
                "source": source,
                "request_body": request_str,
                "response_body": response_str,
                "error": error,
                "attempts": attempts,
            }
        )

    monkeypatch.setattr(hb, "_record_trace", fake_record)
    return calls


def test_no_log_record_contains_response_body(
    monkeypatch: pytest.MonkeyPatch,
    _ollama_ok,
    _capture_trace,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG, logger="ratchet.hermes")
    monkeypatch.delenv("HERMES_LOG_PAYLOADS", raising=False)  # default: false

    out = hb._call_ollama("sys", "user", trace_source="chat")
    assert out == SENTINEL

    # The sentinel must NOT appear anywhere in the captured logs.
    for rec in caplog.records:
        assert SENTINEL not in rec.getMessage(), (
            f"PR-1 A3: response body leaked via log record: {rec.getMessage()!r}"
        )


def test_dataset_synth_source_redacts_response_body(
    monkeypatch: pytest.MonkeyPatch, _ollama_ok, _capture_trace
):
    hb._call_ollama("sys", "user", trace_source="skill:dataset_synth")
    assert len(_capture_trace) == 1
    body = _capture_trace[0]["response_body"]
    assert SENTINEL not in body
    assert "redacted" in body.lower()


def test_chat_source_persists_response_body(
    monkeypatch: pytest.MonkeyPatch, _ollama_ok, _capture_trace
):
    # "chat" is NOT in the default redact list — body should be persisted verbatim.
    hb._call_ollama("sys", "user", trace_source="chat")
    body = _capture_trace[0]["response_body"]
    assert SENTINEL in body


def test_log_payloads_kill_switch_default_off(
    monkeypatch: pytest.MonkeyPatch,
    _ollama_ok,
    _capture_trace,
    caplog: pytest.LogCaptureFixture,
):
    """HERMES_LOG_PAYLOADS=false (default) → no body in any log level."""
    caplog.set_level(logging.DEBUG, logger="ratchet.hermes")
    monkeypatch.setenv("HERMES_LOG_PAYLOADS", "false")
    hb._call_ollama("sys", "user", trace_source="chat")
    for rec in caplog.records:
        assert SENTINEL not in rec.getMessage()


def test_log_payloads_opt_in_emits_at_debug_only(
    monkeypatch: pytest.MonkeyPatch,
    _ollama_ok,
    _capture_trace,
    caplog: pytest.LogCaptureFixture,
):
    """When opted-in, body appears at DEBUG (developer-only) — not at INFO."""
    caplog.set_level(logging.DEBUG, logger="ratchet.hermes")
    monkeypatch.setenv("HERMES_LOG_PAYLOADS", "true")
    hb._call_ollama("sys", "user", trace_source="chat")

    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert not any(SENTINEL in r.getMessage() for r in info_records), (
        "INFO-level log must never carry the body, even when payload logging is opted-in"
    )
    assert any(SENTINEL in r.getMessage() for r in debug_records), (
        "DEBUG-level log should carry the body when HERMES_LOG_PAYLOADS=true"
    )


def test_store_payloads_off_blanks_all_bodies(
    monkeypatch: pytest.MonkeyPatch, _ollama_ok, _capture_trace
):
    """HERMES_TRACE_STORE_PAYLOADS=false → bodies blanked regardless of source."""
    monkeypatch.setenv("HERMES_TRACE_STORE_PAYLOADS", "false")
    hb._call_ollama("sys", "user", trace_source="chat")
    body = _capture_trace[0]["response_body"]
    assert SENTINEL not in body
    assert "redacted" in body.lower()
