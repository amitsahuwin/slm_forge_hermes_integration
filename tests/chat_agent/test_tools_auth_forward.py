"""Chat tools must authenticate when calling back into the SLM-Forge API.

The chat agent's LangChain tools (``packages/chat_agent/tools.py``) run
inside the FastAPI process *and* call back out to ``localhost:8000`` over
HTTP. With ``SLM_FORGE_AUTH_ENABLED=true`` the AuthMiddleware sees no
bearer / service token on those internal calls and 401s them — which
surfaces in the chat UI as the user-visible "authentication issue".

Fix: forward the active request's bearer token to outbound tool calls
via a new ``bearer_token_ctx`` contextvar bound by ``AuthMiddleware``
after JWT verification. The ``_client()`` factory reads that contextvar
and attaches an ``Authorization`` header (or, in worker contexts where
the contextvar is unset but ``SLM_FORGE_SERVICE_TOKEN`` is configured,
falls back to ``X-Service-Token``). When neither is available — the
default dev mode — we send no auth headers, matching today's behaviour.

These tests pin that contract before the bridge change lands.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from packages.chat_agent import tools as chat_tools


def _capture_request_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``chat_tools._client`` to use an in-process transport that
    captures the outbound headers for inspection. Returns a dict that gets
    populated with ``{"headers": dict, "url": str}`` after the call."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        # Mirror the shape ``list_datasets`` expects.
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)

    def fake_client() -> httpx.Client:
        return httpx.Client(
            base_url=chat_tools.API_URL,
            timeout=chat_tools._DEFAULT_TIMEOUT,
            transport=transport,
        )

    monkeypatch.setattr(chat_tools, "_client", fake_client)
    return captured


def test_safe_get_forwards_bearer_when_ctxvar_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a request's JWT has been bound to ``bearer_token_ctx``, every
    outbound tool call must echo it in the ``Authorization`` header so the
    AuthMiddleware on the receiving end can let the call through with the
    user's own permissions (not a synthetic admin)."""
    from packages._log_context import bearer_token_ctx

    captured = _capture_request_headers(monkeypatch)
    token = bearer_token_ctx.set("eyJhbGciOi.fake.jwt")
    try:
        chat_tools._safe_get("/api/v1/datasets")
    finally:
        bearer_token_ctx.reset(token)

    assert captured["headers"].get("authorization") == "Bearer eyJhbGciOi.fake.jwt"


def test_safe_post_forwards_bearer_when_ctxvar_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same contract for POST."""
    from packages._log_context import bearer_token_ctx

    captured = _capture_request_headers(monkeypatch)
    token = bearer_token_ctx.set("user-jwt-xyz")
    try:
        chat_tools._safe_post("/api/v1/runs", {"dataset": "demo"})
    finally:
        bearer_token_ctx.reset(token)

    assert captured["headers"].get("authorization") == "Bearer user-jwt-xyz"


def test_falls_back_to_service_token_when_ctxvar_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In worker contexts (no request → no contextvar) but with
    ``SLM_FORGE_SERVICE_TOKEN`` configured, outbound calls use the existing
    service-account bypass header. Matches how ratchet / trainer talk to
    the API today."""
    from packages._log_context import bearer_token_ctx

    captured = _capture_request_headers(monkeypatch)
    monkeypatch.setenv("SLM_FORGE_SERVICE_TOKEN", "svc-secret")
    tok = bearer_token_ctx.set(None)
    try:
        chat_tools._safe_get("/api/v1/datasets")
    finally:
        bearer_token_ctx.reset(tok)

    assert captured["headers"].get("x-service-token") == "svc-secret"
    assert "authorization" not in captured["headers"]


def test_bearer_wins_over_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """When BOTH are present (a misconfigured deployment), prefer the user's
    JWT — it's the more specific signal and gives least-privilege rather
    than admin-bypass behaviour."""
    from packages._log_context import bearer_token_ctx

    captured = _capture_request_headers(monkeypatch)
    monkeypatch.setenv("SLM_FORGE_SERVICE_TOKEN", "svc-secret")
    tok = bearer_token_ctx.set("user-jwt")
    try:
        chat_tools._safe_get("/api/v1/datasets")
    finally:
        bearer_token_ctx.reset(tok)

    assert captured["headers"].get("authorization") == "Bearer user-jwt"
    assert "x-service-token" not in captured["headers"]


def test_no_headers_when_disabled_and_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default dev mode (auth off, no service token, no contextvar):
    outbound calls carry no auth headers. Matches today's behaviour, so
    existing local-dev workflows aren't disrupted."""
    from packages._log_context import bearer_token_ctx

    captured = _capture_request_headers(monkeypatch)
    monkeypatch.delenv("SLM_FORGE_SERVICE_TOKEN", raising=False)
    tok = bearer_token_ctx.set(None)
    try:
        chat_tools._safe_get("/api/v1/datasets")
    finally:
        bearer_token_ctx.reset(tok)

    assert "authorization" not in captured["headers"]
    assert "x-service-token" not in captured["headers"]
