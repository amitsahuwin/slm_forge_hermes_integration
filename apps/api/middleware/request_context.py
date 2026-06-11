"""Per-request correlation-ID middleware.

For every HTTP request we:
  1. Generate a random ``request_id`` (UUID4 hex, 32 chars).
  2. Resolve a ``user_id`` — placeholder for a future JWT auth layer.
     Today this just reads ``SLM_FORGE_DEFAULT_USER`` (defaults to
     ``anonymous``).
  3. Bind both into the ``packages._log_context`` contextvars so every
     downstream log line emitted while serving the request carries them.
  4. Echo the request_id back in the ``X-Request-ID`` response header so
     clients (curl, the React app, dashboards) can correlate.

Cheap, no I/O, no dependencies beyond starlette.
"""
from __future__ import annotations

import os
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages._log_context import bind, reset

_DEFAULT_USER = os.environ.get("SLM_FORGE_DEFAULT_USER", "anonymous")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind ``request_id`` and ``user_id`` into log context for the request."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Prefer a client-supplied ID (for end-to-end tracing through a proxy)
        # but fall back to a fresh one so we always emit something.
        incoming = request.headers.get("x-request-id", "").strip()
        request_id = incoming or uuid4().hex

        # Phase M: the auth middleware (which runs INSIDE this one) will
        # populate request.state.user once it has resolved a JWT (or the
        # synthetic admin in disabled mode). We can't read that here
        # because middleware order means AuthMiddleware hasn't dispatched
        # yet — so we bind the default first, then patch the binding
        # *after* call_next has set state.user. The log-context helper
        # accepts a re-bind so subsequent log lines pick up the real user.
        user_id = _DEFAULT_USER

        tokens = bind(request_id=request_id, user_id=user_id)
        try:
            response: Response = await call_next(request)
            # Best-effort re-bind for downstream log emitters running after
            # this point (response middleware, exception handlers).
            user = getattr(request.state, "user", None)
            if user is not None and getattr(user, "id", None):
                # Re-binding overrides the contextvar in this task scope.
                bind(user_id=user.id)
        finally:
            reset(tokens)

        response.headers["X-Request-ID"] = request_id
        return response
