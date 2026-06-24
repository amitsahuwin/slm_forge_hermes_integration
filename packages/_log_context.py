"""Context-local correlation IDs for structured logging.

The values bound here are picked up by ``JsonFormatter`` in
``packages._logging`` and emitted on every log line so we can grep across
all services for a single ``run_id`` or ``request_id``.

Usage
-----
::

    from packages._log_context import bind, reset

    tokens = bind(request_id="abc123", run_id=42)
    try:
        do_work()
    finally:
        reset(tokens)

All bind() arguments are optional — pass only the ones you have.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any

# Each ContextVar's default is None so missing fields are simply omitted
# from the JSON log line rather than serialised as "null".
request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_request_id", default=None
)
user_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_user_id", default=None
)
run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_run_id", default=None
)
session_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_session_id", default=None
)
trace_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_trace_id", default=None
)
tenant_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_tenant_id", default=None
)
# Active request's verified bearer token (JWT). Bound by AuthMiddleware
# after successful JWT verification so downstream code that calls back
# into the API on the user's behalf — most notably chat-agent tools —
# can forward the user's identity instead of falling back to a synthetic
# admin or service-account bypass.
bearer_token_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_bearer_token", default=None
)

_NAME_TO_VAR: dict[str, contextvars.ContextVar[str | None]] = {
    "request_id": request_id_ctx,
    "user_id": user_id_ctx,
    "run_id": run_id_ctx,
    "session_id": session_id_ctx,
    "trace_id": trace_id_ctx,
    "tenant_id": tenant_id_ctx,
}


def bind(
    *,
    request_id: str | int | None = None,
    user_id: str | int | None = None,
    run_id: str | int | None = None,
    session_id: str | int | None = None,
    trace_id: str | int | None = None,
    tenant_id: str | int | None = None,
) -> dict[str, contextvars.Token[str | None]]:
    """Set provided IDs on their ContextVars and return reset tokens.

    All values are stringified so integer IDs round-trip cleanly into the
    JSON formatter.
    """
    pairs: dict[str, str | int | None] = {
        "request_id": request_id,
        "user_id": user_id,
        "run_id": run_id,
        "session_id": session_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
    }
    tokens: dict[str, contextvars.Token[str | None]] = {}
    for name, value in pairs.items():
        if value is None:
            continue
        tokens[name] = _NAME_TO_VAR[name].set(str(value))
    return tokens


def reset(tokens: dict[str, contextvars.Token[str | None]]) -> None:
    """Undo a prior ``bind()`` using its returned tokens."""
    for name, token in tokens.items():
        var = _NAME_TO_VAR.get(name)
        if var is None:
            continue
        try:
            var.reset(token)
        except ValueError:
            # Token was created in a different Context; silently drop —
            # this happens when bind() is called from one task and reset()
            # from another. Better than raising in a finally clause.
            pass


@contextlib.contextmanager
def binding(
    *,
    request_id: str | int | None = None,
    user_id: str | int | None = None,
    run_id: str | int | None = None,
    session_id: str | int | None = None,
    trace_id: str | int | None = None,
    tenant_id: str | int | None = None,
) -> Iterator[None]:
    """Scoped ``bind`` — ``with binding(run_id=42): ...`` and the ContextVars
    are reset on exit. Eliminates the bind/try-finally/reset boilerplate at
    every call site (ratchet loop, FastAPI dependencies, skill endpoints)."""
    tokens = bind(
        request_id=request_id,
        user_id=user_id,
        run_id=run_id,
        session_id=session_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
    )
    try:
        yield
    finally:
        reset(tokens)


def current() -> dict[str, Any]:
    """Return a dict of all non-None contextvar values.

    Used by ``JsonFormatter`` to enrich every log record.
    """
    out: dict[str, Any] = {}
    for name, var in _NAME_TO_VAR.items():
        v = var.get()
        if v is not None:
            out[name] = v
    return out
