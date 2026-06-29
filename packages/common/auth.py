"""Phase C R4 — Keycloak ``client_credentials`` token for host workers.

The trainer / ratchet / exporter workers authenticate to the API with a
service-account JWT. Each worker boots, fetches a token from Keycloak's
``/realms/{realm}/protocol/openid-connect/token`` endpoint, caches it
until just before ``exp``, and presents it as ``Authorization: Bearer``
on every API call.

This replaces the legacy ``X-Service-Token`` shared-secret bypass: with
a real JWT, the API maps the worker into the same ``Identity`` system
as human users (``role=worker``, ``tenant_id=system``), and OPA's
worker scope (see ``policies/role_matrix.rego``) narrowly constrains
what they may do.

Env contract:
  SLM_FORGE_KEYCLOAK_URL           e.g. http://keycloak:8080
  SLM_FORGE_KEYCLOAK_REALM         e.g. slm-forge
  SLM_FORGE_WORKER_CLIENT_ID       default: slm-forge-worker
  SLM_FORGE_WORKER_CLIENT_SECRET   confidential — never log or commit
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("worker.auth")

DEFAULT_CLIENT_ID = "slm-forge-worker"


@dataclass
class _CachedToken:
    bearer: str
    expires_at: float  # epoch seconds


class WorkerToken:
    """Fetches and caches a Keycloak service-account JWT.

    Thread-safe. The cache is per-instance — workers should instantiate
    once at boot and pass the instance around (or use the module-level
    :data:`_default` via :func:`get_default`).
    """

    def __init__(self, leeway_seconds: int = 60) -> None:
        self._leeway = leeway_seconds
        self._token: _CachedToken | None = None
        self._lock = threading.Lock()

    def bearer(self) -> str:
        """Return the current JWT, refreshing if expired or near-expiry."""
        with self._lock:
            now = time.time()
            if self._token is not None and self._token.expires_at - self._leeway > now:
                return self._token.bearer
            self._token = self._fetch()
            return self._token.bearer

    def invalidate(self) -> None:
        """Force the next ``bearer()`` to re-fetch. Used when the API
        returns 401 — the cached token may have been revoked early."""
        with self._lock:
            self._token = None

    # --- internals -----------------------------------------------------------

    def _fetch(self) -> _CachedToken:
        kc_url = os.environ.get("SLM_FORGE_KEYCLOAK_URL", "").rstrip("/")
        realm = os.environ.get("SLM_FORGE_KEYCLOAK_REALM", "").strip()
        client_id = os.environ.get(
            "SLM_FORGE_WORKER_CLIENT_ID", DEFAULT_CLIENT_ID
        ).strip()
        client_secret = os.environ.get("SLM_FORGE_WORKER_CLIENT_SECRET", "").strip()

        if not kc_url:
            raise RuntimeError("SLM_FORGE_KEYCLOAK_URL is not set")
        if not realm:
            raise RuntimeError("SLM_FORGE_KEYCLOAK_REALM is not set")
        if not client_secret:
            raise RuntimeError("SLM_FORGE_WORKER_CLIENT_SECRET is not set")

        token_endpoint = f"{kc_url}/realms/{realm}/protocol/openid-connect/token"
        log.debug("worker token: requesting from %s as %s", token_endpoint, client_id)
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        body = resp.json()
        bearer = body["access_token"]
        exp = _decode_exp(bearer)
        if exp is None:
            # Fall back to ``expires_in`` if the JWT payload is opaque.
            exp = time.time() + float(body.get("expires_in", 300))
        log.info("worker token: acquired (exp in %.0fs)", exp - time.time())
        return _CachedToken(bearer=bearer, expires_at=exp)


def _decode_exp(jwt: str) -> float | None:
    """Read ``exp`` from a JWT payload. Returns ``None`` on malformed token.

    We do NOT verify the signature here — the worker just carries the
    token forward to the API, which is the authoritative verifier.
    """
    try:
        _, payload_b64, _ = jwt.split(".", 2)
    except ValueError:
        return None
    # urlsafe + missing padding handling
    pad = "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    return float(exp) if exp is not None else None


_default: WorkerToken | None = None
_default_lock = threading.Lock()


def get_default() -> WorkerToken:
    """Module-level cached :class:`WorkerToken`. Workers should share one
    instance so the underlying cache survives across HTTP calls."""
    global _default
    with _default_lock:
        if _default is None:
            _default = WorkerToken()
        return _default