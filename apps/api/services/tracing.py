"""Phase B — `trace_span` context manager for hermes_traces nesting.

Every span gets a `trace_id` (root) or inherits one (child). Each row
stores its `parent_span_id`, its own `span_id`, an optional
`agent_run_id`, and a `kind` discriminator (agent | skill | tool).

Usage::

    with trace_span(kind="agent", name="experiment_recommender") as span:
        ...
        with trace_span(kind="skill", name="propose_hyperparam_mutation"):
            ...
        span.set_result({"recommendation": "..."})

The context manager is synchronous so it composes with both sync and
async call sites (the body of `with` does not await on the span itself).
Internal DB write uses the existing engine from
`apps.api.services.db.engine`; the writer is best-effort: any DB error
is logged at DEBUG and never crashes the caller, matching the existing
``_record_trace`` behaviour in ``packages.ratchet.hermes_bridge``.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

log = logging.getLogger(__name__)

SpanKind = Literal["agent", "skill", "tool"]

# Stack of (trace_id, span_id) tuples. Innermost on the right (-1).
# Stored as an immutable tuple so each ContextVar update is a swap, not
# an in-place mutation — required for correct contextvar semantics
# across asyncio.Task boundaries.
_span_stack: contextvars.ContextVar[tuple[tuple[str, str], ...]] = contextvars.ContextVar(
    "slm_forge_span_stack", default=()
)


def _new_id() -> str:
    """Return a short hex id (16 chars) — UUIDv4 truncated. We avoid the
    dashes so node IDs remain alphanumeric for any downstream consumer."""
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    kind: SpanKind
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    agent_run_id: str | None
    started_at: datetime
    attrs: dict[str, Any] = field(default_factory=dict)
    _result: Any = None
    _row_id: int | None = None

    def set_result(self, value: Any) -> None:
        """Capture the span's logical result; serialised into
        ``response_body`` when the row is finalised on exit."""
        self._result = value


@contextlib.contextmanager
def trace_span(
    kind: SpanKind,
    name: str,
    *,
    run_id: int | None = None,
    session_id: int | None = None,
    agent_run_id: str | None = None,
    request_body: dict[str, Any] | None = None,
    **attrs: Any,
):
    """Open a span; insert a hermes_traces row on enter, update on exit.

    The contextvar stack is pushed on enter and popped in a ``finally``
    so an exception inside the body still pops correctly — the next
    root span starts cleanly.
    """
    stack = _span_stack.get()
    parent = stack[-1] if stack else None
    trace_id = parent[0] if parent else _new_id()
    span_id = _new_id()
    parent_span_id = parent[1] if parent else None
    # Inherit the outer agent_run_id if the caller did not pass one.
    if agent_run_id is None and stack:
        # Walk the stack for the nearest carrier; lookups are tiny.
        inherited = _current_agent_run_id()
        agent_run_id = inherited

    started_at = datetime.now(UTC)
    monotonic_start = time.monotonic()
    span = Span(
        kind=kind,
        name=name,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_run_id=agent_run_id,
        started_at=started_at,
        attrs=dict(attrs),
    )
    span._row_id = _insert_open_row(
        span=span,
        run_id=run_id,
        session_id=session_id,
        request_body=request_body,
    )

    token = _span_stack.set(stack + ((trace_id, span_id),))
    # Track agent_run_id alongside the stack so children inherit it.
    if agent_run_id is not None:
        _agent_run_id_ctx.set(agent_run_id)

    error: str | None = None
    try:
        yield span
    except BaseException as e:
        error = str(e) or e.__class__.__name__
        raise
    finally:
        try:
            _finalize_row(
                span=span,
                duration_ms=int((time.monotonic() - monotonic_start) * 1000),
                error=error,
            )
        finally:
            _span_stack.reset(token)


# --- internals ---------------------------------------------------------------

_agent_run_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "slm_forge_agent_run_id", default=None
)


def _current_agent_run_id() -> str | None:
    try:
        return _agent_run_id_ctx.get()
    except LookupError:
        return None


def _insert_open_row(
    *,
    span: Span,
    run_id: int | None,
    session_id: int | None,
    request_body: dict[str, Any] | None,
) -> int | None:
    """Insert the row at span open; returns the row id (None on best-effort failure)."""
    try:
        # Lazy import: avoids cycles + keeps worker callers safe when DB
        # isn't reachable (mirrors hermes_bridge._record_trace).
        from sqlmodel import Session as _Session

        from apps.api.models.hermes_trace import HermesTrace
        from apps.api.services import db as _db
        from apps.api.services.tenant import current_tenant

        body_str = (
            json.dumps(request_body, ensure_ascii=False, default=str)
            if request_body
            else ""
        )
        with _Session(_db.engine) as s:
            row = HermesTrace(
                source=span.name,
                kind=span.kind,
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                agent_run_id=span.agent_run_id,
                request_body=body_str,
                response_body="",
                error=None,
                duration_ms=0,
                tenant_id=current_tenant(),
                run_id=run_id,
                session_id=session_id,
                success=True,
                skill_name=span.name if span.kind == "skill" else None,
                created_at=span.started_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row.id
    except Exception as e:
        log.debug("trace_span open row insert skipped (%s)", e)
        return None


def _finalize_row(*, span: Span, duration_ms: int, error: str | None) -> None:
    """Update the row at span close with duration / response / error."""
    if span._row_id is None:
        return
    try:
        from sqlmodel import Session as _Session

        from apps.api.models.hermes_trace import HermesTrace
        from apps.api.services import db as _db

        response_body = ""
        if span._result is not None:
            try:
                response_body = json.dumps(span._result, ensure_ascii=False, default=str)
            except Exception:  # pragma: no cover — extremely unusual values
                response_body = str(span._result)
        with _Session(_db.engine) as s:
            row = s.get(HermesTrace, span._row_id)
            if row is None:
                return
            row.duration_ms = duration_ms
            row.response_body = response_body
            row.error = error
            row.success = error is None
            s.add(row)
            s.commit()
    except Exception as e:
        log.debug("trace_span close row update skipped (%s)", e)