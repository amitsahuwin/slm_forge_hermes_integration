"""JWT + OPA authentication middleware (Phase M).

This module wires three things together:

1.  ``AuthMiddleware`` — a Starlette middleware that, when enforcement is
    enabled, verifies an incoming ``Authorization: Bearer <jwt>`` header
    against the Keycloak realm's JWKS and attaches the resulting
    :class:`User` to ``request.state.user``. When enforcement is *off*
    (the default), it skips JWT work entirely and attaches a synthetic
    admin user so the rest of the API behaves exactly like before
    Phase M shipped.

2.  ``policy_check`` — POSTs the user + action + resource to OPA and
    returns ``(allow, reason)``. Fail-closed only when enforcement is on
    (i.e. an unreachable OPA is treated as a denial in production but as
    an allow in disabled mode so the dev loop keeps working).

3.  ``requires(action, resource)`` — a decorator factory that you slap
    onto destructive/admin endpoints. It's a no-op when enforcement is
    off, and raises ``HTTPException(403, reason)`` when OPA says no.

Only endpoints you decorate get OPA-checked — the middleware never does
per-route policy checks on its own. That keeps unannotated read-only
endpoints fully open even after you flip the flag on, so you can roll out
enforcement incrementally.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from apps.api.services.auth_settings import AuthSettings, get_auth_settings

log = logging.getLogger("slm_forge.auth")

# Endpoints that must work without a JWT even when enforcement is on, so
# health probes / docs / the login bootstrap keep working.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/api/v1/health",
        "/api/v1/chat/health",
        "/api/v1/auth/config",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/favicon.ico",
    }
)


class User(BaseModel):
    """Minimal user shape we pass around inside the API."""

    id: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


# ─── JWKS cache ─────────────────────────────────────────────────────────────
# Keycloak rotates signing keys infrequently — caching the JWKS for 5
# minutes is the standard tradeoff between perf and key-rotation responsiveness.

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0, "url": ""}
_JWKS_TTL_SECONDS = 300.0


def _fetch_jwks(jwks_url: str) -> dict[str, Any]:
    """Fetch (and 5-minute-cache) the realm's JWKS document."""
    now = time.monotonic()
    if (
        _JWKS_CACHE["keys"] is not None
        and _JWKS_CACHE["url"] == jwks_url
        and (now - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS
    ):
        return _JWKS_CACHE["keys"]  # type: ignore[no-any-return]
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(jwks_url)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("JWKS fetch failed url=%s err=%s", jwks_url, e)
        raise HTTPException(503, "Identity provider unreachable") from e
    _JWKS_CACHE["keys"] = data
    _JWKS_CACHE["fetched_at"] = now
    _JWKS_CACHE["url"] = jwks_url
    return data


def _clear_jwks_cache() -> None:
    """Test hook — wipe the JWKS cache so a fresh fetch is performed."""
    _JWKS_CACHE["keys"] = None
    _JWKS_CACHE["fetched_at"] = 0.0
    _JWKS_CACHE["url"] = ""


# ─── JWT verification ───────────────────────────────────────────────────────


def verify_jwt(token: str, settings: AuthSettings | None = None) -> User:
    """Verify an RS256 access token against Keycloak's JWKS.

    Returns a populated :class:`User`. Raises ``HTTPException(401)`` on any
    failure — bad signature, expired, malformed, wrong issuer, missing kid,
    you name it. The exception detail is deliberately generic to avoid
    leaking which check failed to a probing client.
    """
    cfg = settings or get_auth_settings()

    # Import lazily so the dependency is only required when enforcement is on.
    try:
        from jose import jwt
        from jose.exceptions import JWTError
    except ImportError as e:  # pragma: no cover
        raise HTTPException(
            503,
            "Auth dependency 'python-jose' missing — install the [auth] extra.",
        ) from e

    if not token or "." not in token:
        raise HTTPException(401, "Invalid bearer token")

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(401, "Invalid bearer token") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(401, "Token missing key id")

    jwks = _fetch_jwks(cfg.jwks_url)
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        # Key rotation? Bust the cache once and try a fresh fetch before giving up.
        _clear_jwks_cache()
        jwks = _fetch_jwks(cfg.jwks_url)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise HTTPException(401, "Unknown signing key")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            # Keycloak's `aud` claim is unreliable for resource-server access
            # tokens (it often only lists the auth client), so we skip the
            # audience check and rely on issuer + signature + expiry.
            options={"verify_aud": False},
            issuer=cfg.issuer,
        )
    except JWTError as e:
        log.info("JWT validation failed: %s", e)
        raise HTTPException(401, "Invalid bearer token") from e

    realm_access = claims.get("realm_access") or {}
    roles = list(realm_access.get("roles") or [])
    groups = list(claims.get("groups") or [])
    return User(
        id=str(claims.get("preferred_username") or claims.get("sub") or "unknown"),
        email=claims.get("email"),
        roles=roles,
        groups=groups,
    )


# ─── OPA policy check ───────────────────────────────────────────────────────


def policy_check(
    user: User,
    action: str,
    resource: str,
    settings: AuthSettings | None = None,
) -> tuple[bool, str | None]:
    """Ask OPA whether ``user`` may perform ``action`` on ``resource``.

    Returns ``(allow, reason)``. ``reason`` is a human-readable string OPA
    populates for denials; we surface it as the 403 detail so the UI can
    show something better than "Forbidden."

    Behaviour:

    * If enforcement is off → ``(True, None)`` immediately. No OPA call.
    * If OPA returns ``allow=true`` → ``(True, None)``.
    * If OPA returns ``allow=false`` → ``(False, reason_from_opa)``.
    * If OPA is unreachable AND enforcement is on → ``(False, "policy
      engine unreachable")``. Fail-closed.
    """
    cfg = settings or get_auth_settings()
    if not cfg.auth_enabled:
        return True, None

    payload = {
        "input": {
            "user": {"id": user.id, "roles": user.roles, "groups": user.groups},
            "action": action,
            "resource": resource,
        }
    }
    url = f"{cfg.opa_url.rstrip('/')}{cfg.opa_decision_path}"
    try:
        with httpx.Client(timeout=cfg.opa_timeout_seconds) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("OPA unreachable url=%s err=%s — failing CLOSED", url, e)
        return False, "policy engine unreachable"

    result = doc.get("result")
    # OPA's `/v1/data/slm_forge/allow` returns the value of the `allow` rule
    # directly when only that rule is queried. We also handle the structured
    # form where the API exposes a richer object with allow+reason.
    if isinstance(result, bool):
        return result, None if result else f"denied: {action} on {resource}"
    if isinstance(result, dict):
        allow = bool(result.get("allow", False))
        reason = result.get("reason")
        if not allow and not reason:
            reason = f"denied: {action} on {resource}"
        return allow, reason
    return False, "policy engine returned unexpected shape"


# ─── Middleware ─────────────────────────────────────────────────────────────


def _synthetic_admin(settings: AuthSettings) -> User:
    """Build the all-powerful local-dev user. Used when auth is disabled."""
    return User(
        id=settings.default_user,
        email=None,
        roles=["admin"],
        groups=[],
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """Attach ``request.state.user`` and (when enabled) verify JWTs.

    The middleware does **not** do per-route policy enforcement; that's the
    job of the :func:`requires` decorator. That separation means unannotated
    endpoints stay open in mixed-mode, which is what we want during the
    Phase M rollout.
    """

    async def dispatch(  # type: ignore[override]
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_auth_settings()

        # Disabled mode — every request gets the synthetic admin and we pass
        # through. This keeps the local-dev workflow bit-for-bit unchanged.
        if not settings.auth_enabled:
            request.state.user = _synthetic_admin(settings)
            return await call_next(request)

        # Public endpoints (health, docs, /auth/config bootstrap) skip JWT.
        if _is_public_path(request.url.path):
            request.state.user = _synthetic_admin(settings)
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return _json_401("Missing bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        try:
            user = verify_jwt(token, settings=settings)
        except HTTPException as e:
            return _json_response(e.status_code, str(e.detail))

        request.state.user = user
        return await call_next(request)


def _is_public_path(path: str) -> bool:
    """Match exact + a couple of well-known prefixes (docs assets)."""
    if path in PUBLIC_PATHS:
        return True
    # Swagger/OpenAPI/Redoc fetch assets under these prefixes.
    return path.startswith("/docs") or path.startswith("/redoc") or path.startswith(
        "/openapi"
    )


def _json_401(detail: str) -> Response:
    return _json_response(401, detail)


def _json_response(status: int, detail: str) -> Response:
    """Tiny JSON-error helper so the middleware doesn't need fastapi imports."""
    import json as _json

    body = _json.dumps({"detail": detail}).encode("utf-8")
    return Response(content=body, status_code=status, media_type="application/json")


# ─── @requires decorator ────────────────────────────────────────────────────


def requires(action: str, resource: str) -> Callable[..., Any]:
    """Decorator: gate a FastAPI endpoint behind an OPA decision.

    Usage::

        @router.delete("/{run_id}", status_code=204)
        @requires("delete", "run")
        def delete_run(run_id: int, request: Request, session: SessionDep) -> None:
            ...

    The decorated function MUST accept a ``request: Request`` parameter so
    we can pull ``request.state.user``. We don't introspect ``inspect.signature``
    at call time — explicit is better than magic, and FastAPI will already
    have validated the dependency.

    When enforcement is off, the decorator is a transparent pass-through.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import asyncio

        if asyncio.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _enforce(action, resource, kwargs)
                return await fn(*args, **kwargs)

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _enforce(action, resource, kwargs)
            return fn(*args, **kwargs)

        return sync_wrapper

    return decorator


def _enforce(action: str, resource: str, kwargs: dict[str, Any]) -> None:
    """Shared body for the sync + async wrappers in :func:`requires`."""
    settings = get_auth_settings()
    if not settings.auth_enabled:
        return  # No-op in disabled mode.

    request: Request | None = kwargs.get("request")
    if request is None:
        # Defensive: a decorated endpoint forgot to declare ``request: Request``.
        log.error(
            "@requires(%s, %s) used on an endpoint without `request: Request`",
            action,
            resource,
        )
        raise HTTPException(500, "Endpoint misconfigured (no request)")

    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Not authenticated")

    allow, reason = policy_check(user, action, resource, settings=settings)
    if not allow:
        raise HTTPException(403, reason or f"Forbidden: {action} on {resource}")
