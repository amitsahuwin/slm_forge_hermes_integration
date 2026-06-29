"""Phase C R4 — ``WorkerToken`` fetches a Keycloak service-account JWT.

Workers (trainer / ratchet / exporter) authenticate to the API via the
OIDC ``client_credentials`` grant against the realm's ``slm-forge-worker``
client. Token is cached until ``exp - leeway``; refreshed lazily on next
``bearer()`` call.

Tests run against a fake token endpoint to keep them offline.
"""
from __future__ import annotations

import json
import time
from typing import Any

import pytest


def _make_jwt(exp: int) -> str:
    """Return a plausibly-shaped (but unsigned) JWT whose payload carries
    the given ``exp``. ``WorkerToken`` only decodes the payload; it does
    NOT verify the signature (the *API* verifies; the *worker* just
    forwards). So a base64-only token is sufficient for these tests."""
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp, "preferred_username": "service-account-worker"}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


@pytest.fixture()
def fake_keycloak(monkeypatch: pytest.MonkeyPatch):
    """Capture the token endpoint URL + return a deterministic JWT."""
    calls: list[dict[str, Any]] = []
    state = {"exp_offset": 1800, "fail": False}

    def fake_post(url: str, data: dict, timeout: float, **kwargs) -> Any:  # noqa: ARG001
        calls.append({"url": url, "data": dict(data)})
        if state["fail"]:
            raise RuntimeError("network down")

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                jwt = _make_jwt(int(time.time()) + state["exp_offset"])
                return {"access_token": jwt, "expires_in": state["exp_offset"]}

        return _Resp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "post", fake_post)
    return {"calls": calls, "state": state}


def _set_env(monkeypatch):
    monkeypatch.setenv("SLM_FORGE_KEYCLOAK_URL", "http://kc.local:8080")
    monkeypatch.setenv("SLM_FORGE_KEYCLOAK_REALM", "slm-forge")
    monkeypatch.setenv("SLM_FORGE_WORKER_CLIENT_ID", "slm-forge-worker")
    monkeypatch.setenv("SLM_FORGE_WORKER_CLIENT_SECRET", "dev-secret")


def test_worker_token_fetches_from_keycloak(fake_keycloak, monkeypatch):
    from packages.common.auth import WorkerToken

    _set_env(monkeypatch)
    tok = WorkerToken()
    bearer = tok.bearer()
    assert bearer.startswith("eyJ") or bearer.count(".") == 2
    assert len(fake_keycloak["calls"]) == 1
    call = fake_keycloak["calls"][0]
    assert call["url"].endswith("/realms/slm-forge/protocol/openid-connect/token")
    assert call["data"]["grant_type"] == "client_credentials"
    assert call["data"]["client_id"] == "slm-forge-worker"
    assert call["data"]["client_secret"] == "dev-secret"


def test_worker_token_is_cached_until_near_exp(fake_keycloak, monkeypatch):
    from packages.common.auth import WorkerToken

    _set_env(monkeypatch)
    fake_keycloak["state"]["exp_offset"] = 1800
    tok = WorkerToken(leeway_seconds=60)
    a = tok.bearer()
    b = tok.bearer()
    c = tok.bearer()
    assert a == b == c
    assert len(fake_keycloak["calls"]) == 1, "cached token must not re-fetch"


def test_worker_token_refreshes_near_expiry(fake_keycloak, monkeypatch):
    from packages.common.auth import WorkerToken

    _set_env(monkeypatch)
    # First call returns a token that is already past leeway, forcing the
    # NEXT bearer() to refresh.
    fake_keycloak["state"]["exp_offset"] = 30  # exp 30s away
    tok = WorkerToken(leeway_seconds=60)        # leeway 60s → already expired
    tok.bearer()  # 1st fetch
    tok.bearer()  # forces refresh
    assert len(fake_keycloak["calls"]) == 2


def test_worker_token_raises_when_env_missing(monkeypatch):
    from packages.common.auth import WorkerToken

    monkeypatch.delenv("SLM_FORGE_KEYCLOAK_URL", raising=False)
    monkeypatch.delenv("SLM_FORGE_WORKER_CLIENT_SECRET", raising=False)
    tok = WorkerToken()
    with pytest.raises(RuntimeError, match="SLM_FORGE_KEYCLOAK_URL"):
        tok.bearer()


def test_worker_token_propagates_network_error(fake_keycloak, monkeypatch):
    from packages.common.auth import WorkerToken

    _set_env(monkeypatch)
    fake_keycloak["state"]["fail"] = True
    tok = WorkerToken()
    with pytest.raises(RuntimeError, match="network down"):
        tok.bearer()


def test_service_headers_uses_worker_jwt_when_available(fake_keycloak, monkeypatch):
    """The existing ``packages._api_client.service_headers`` should prefer
    a worker JWT (Authorization: Bearer) over the legacy X-Service-Token."""
    from packages._api_client import service_headers

    _set_env(monkeypatch)
    monkeypatch.delenv("SLM_FORGE_SERVICE_TOKEN", raising=False)
    headers = service_headers()
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")