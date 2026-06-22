"""PR-1 A1 — `_call_ollama` retries transient failures with backoff + jitter.

Asserts:
  - A read-timeout-then-success retries and ultimately succeeds.
  - A 502 storm retries the configured number of times then re-raises.
  - 4xx (non-429) is NOT retried.
  - 429 IS retried.
  - `HERMES_MAX_RETRIES` env var caps attempts.
  - Trace row carries the final attempt count.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

import packages.ratchet.hermes_bridge as hb


class _StubResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, status: int, json_payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status
        self._json = json_payload or {"message": {"content": "ok"}}
        self.text = text or "{}"

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=httpx.Request("POST", "http://x"), response=self  # type: ignore[arg-type]
            )


@pytest.fixture(autouse=True)
def _record_trace_disabled(monkeypatch: pytest.MonkeyPatch):
    """Capture trace calls without touching the real DB."""
    calls: list[dict[str, Any]] = []

    def fake_record(**kw):
        calls.append(kw)

    monkeypatch.setattr(hb, "_record_trace", fake_record)
    return calls


@pytest.fixture(autouse=True)
def _short_backoff(monkeypatch: pytest.MonkeyPatch):
    """Crush tenacity's sleep to ~zero so the suite stays fast."""
    monkeypatch.setenv("HERMES_RETRY_BACKOFF_MULT_S", "0.001")


def test_retry_on_read_timeout_then_success(monkeypatch: pytest.MonkeyPatch, _record_trace_disabled):
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("simulated")
        return _StubResponse(200, {"message": {"content": "hello"}})

    monkeypatch.setattr(hb.httpx, "post", fake_post)

    out = hb._call_ollama("system", "user", trace_source="test")

    assert out == "hello"
    assert calls["n"] == 3
    # Exactly one trace row records the logical call, with attempts=3.
    assert len(_record_trace_disabled) == 1
    assert _record_trace_disabled[0].get("attempts") == 3
    assert _record_trace_disabled[0].get("error") is None


def test_retry_storm_on_502_gives_up(monkeypatch: pytest.MonkeyPatch, _record_trace_disabled):
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        return _StubResponse(502, text="bad gateway")

    monkeypatch.setattr(hb.httpx, "post", fake_post)
    monkeypatch.setenv("HERMES_MAX_RETRIES", "3")

    with pytest.raises(httpx.HTTPStatusError):
        hb._call_ollama("system", "user", trace_source="test")

    # 3 attempts max (initial + 2 retries) per the env override.
    assert calls["n"] == 3
    assert len(_record_trace_disabled) == 1
    assert _record_trace_disabled[0].get("attempts") == 3
    assert _record_trace_disabled[0].get("error") is not None


def test_no_retry_on_400(monkeypatch: pytest.MonkeyPatch, _record_trace_disabled):
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        return _StubResponse(400, text="bad request")

    monkeypatch.setattr(hb.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        hb._call_ollama("system", "user", trace_source="test")

    assert calls["n"] == 1, "4xx (non-429) must NOT trigger retries"
    assert len(_record_trace_disabled) == 1
    assert _record_trace_disabled[0].get("attempts") == 1


def test_retries_429(monkeypatch: pytest.MonkeyPatch, _record_trace_disabled):
    """429 (Too Many Requests) is the one 4xx code we DO retry."""
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            return _StubResponse(429, text="rate limited")
        return _StubResponse(200, {"message": {"content": "ok"}})

    monkeypatch.setattr(hb.httpx, "post", fake_post)

    out = hb._call_ollama("system", "user", trace_source="test")
    assert out == "ok"
    assert calls["n"] == 2


def test_max_retries_env_honoured(monkeypatch: pytest.MonkeyPatch, _record_trace_disabled):
    calls = {"n": 0}

    def fake_post(url, json, timeout):
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(hb.httpx, "post", fake_post)
    monkeypatch.setenv("HERMES_MAX_RETRIES", "1")

    with pytest.raises(httpx.ConnectError):
        hb._call_ollama("system", "user", trace_source="test")

    assert calls["n"] == 1, "HERMES_MAX_RETRIES=1 must mean exactly one attempt"
