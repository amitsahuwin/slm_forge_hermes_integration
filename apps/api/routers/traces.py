"""Hermes / Ollama request-response trace inspector.

Admin-only by policy. Returns recent rows from ``hermes_traces`` so the
"Traces" tab can render the JSON bodies side-by-side without scraping
gigabytes of structured logs.

Skill-Activity additions:
  * Filter by skill name (repeatable), success/error, time range,
    minimum duration, run / session.
  * Each ``TraceRow`` carries ``skill_changed: bool`` — ``True`` when this
    row's ``skill_sha256`` differs from the previous trace for the same
    ``skill_name``. Computed via SQL ``LAG()`` window function so the
    answer comes back in one round-trip.
  * ``GET /skills/summary`` aggregates per-skill calls / errors / latency
    so the left sidebar can render the "Skill Activity" panel.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from apps.api.middleware.auth import requires
from apps.api.models.hermes_trace import HermesTrace
from apps.api.services.db import get_session

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


class TraceRow(BaseModel):
    id: int
    created_at: str
    source: str
    model: str
    duration_ms: int
    error: str | None
    request_body: str
    response_body: str
    attempts: int = 1
    tenant_id: str = "default"
    skill_name: str | None = None
    skill_sha256: str | None = None
    skill_mtime: str | None = None
    run_id: int | None = None
    session_id: int | None = None
    success: bool = True
    # Derived: True when this row's ``skill_sha256`` differs from the
    # previous row's for the same ``skill_name``. False when no previous
    # row exists or when there's no skill_name (e.g. ``source='chat'``).
    skill_changed: bool = False
    # Phase B — trace nesting. NULL on legacy rows.
    kind: str = "skill"
    trace_id: str | None = None
    parent_span_id: str | None = None
    span_id: str | None = None
    agent_run_id: str | None = None


class TraceTreeRow(TraceRow):
    """A trace tree: agent (or root skill) span with its child spans inline.

    Returned when the client passes ``?group_by=trace``. ``children`` is
    ordered by ``created_at`` ascending so the Traces tab can render the
    skill calls in the order they happened.
    """

    children: list[TraceRow] = []


class SkillSummaryRow(BaseModel):
    skill_name: str
    calls: int
    errors: int
    avg_duration_ms: int
    p95_duration_ms: int
    first_seen: str
    last_seen: str
    current_sha256: str | None
    # Count of distinct hash transitions for this skill in trace history.
    # A→A→B→C counts as 2 (the A→B and B→C transitions).
    change_count: int


def _row_to_trace(r: HermesTrace, prev_sha: str | None) -> TraceRow:
    """Convert ORM row + prior-hash lookup into the response shape."""
    changed = (
        r.skill_name is not None
        and r.skill_sha256 is not None
        and prev_sha is not None
        and r.skill_sha256 != prev_sha
    )
    return TraceRow(
        id=r.id or 0,
        created_at=r.created_at.isoformat(),
        source=r.source,
        model=r.model,
        duration_ms=r.duration_ms,
        error=r.error,
        request_body=r.request_body,
        response_body=r.response_body,
        attempts=r.attempts,
        tenant_id=r.tenant_id,
        skill_name=r.skill_name,
        skill_sha256=r.skill_sha256,
        skill_mtime=r.skill_mtime.isoformat() if r.skill_mtime else None,
        run_id=r.run_id,
        session_id=r.session_id,
        success=r.success,
        skill_changed=changed,
        kind=getattr(r, "kind", "skill") or "skill",
        trace_id=getattr(r, "trace_id", None),
        parent_span_id=getattr(r, "parent_span_id", None),
        span_id=getattr(r, "span_id", None),
        agent_run_id=getattr(r, "agent_run_id", None),
    )


def _previous_skill_hashes(
    db: Session, row_ids: list[int]
) -> dict[int, str | None]:
    """For each given trace id, return the ``skill_sha256`` of the closest
    earlier trace with the same ``skill_name`` (or ``None`` if there is none).

    A single self-join keeps this O(N log N) on the index.
    """
    if not row_ids:
        return {}
    rows = db.exec(select(HermesTrace).where(HermesTrace.id.in_(row_ids))).all()  # type: ignore[union-attr]
    out: dict[int, str | None] = {}
    for r in rows:
        if r.id is None or r.skill_name is None:
            if r.id is not None:
                out[r.id] = None
            continue
        prev = db.exec(
            select(HermesTrace.skill_sha256)
            .where(HermesTrace.skill_name == r.skill_name)
            .where(HermesTrace.created_at < r.created_at)
            .order_by(desc(HermesTrace.created_at))
            .limit(1)
        ).first()
        out[r.id] = prev


    return out


@router.get("")
@requires("read", "setting")
def list_traces(
    request: Request,
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    source_like: Annotated[
        str | None, Query(description="substring filter on `source`")
    ] = None,
    tenant_id: Annotated[
        str | None, Query(description="exact match on `tenant_id`")
    ] = None,
    skill: Annotated[
        list[str] | None,
        Query(description="repeatable; exact match on parsed skill_name"),
    ] = None,
    status: Annotated[
        str | None, Query(description="`success` or `error`")
    ] = None,
    since: Annotated[
        str | None, Query(description="ISO-8601 lower bound on created_at")
    ] = None,
    until: Annotated[
        str | None, Query(description="ISO-8601 upper bound on created_at")
    ] = None,
    min_duration_ms: Annotated[int | None, Query(ge=0)] = None,
    run_id: Annotated[int | None, Query()] = None,
    session_id: Annotated[int | None, Query()] = None,
    # Phase B
    group_by: Annotated[
        str | None,
        Query(
            description=(
                "When 'trace', collapse rows with the same trace_id into a "
                "tree with each root's children inline. Default: none (flat)."
            )
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Query(description="Filter by span kind: agent | skill | tool"),
    ] = None,
    agent_run_id: Annotated[
        str | None,
        Query(description="Filter to all spans of a single agent invocation"),
    ] = None,
) -> list[TraceRow] | list[TraceTreeRow]:
    """Return the most recent traces, newest first.

    Locked to admin (and any other role granted ``read`` on ``setting``)
    because the request/response bodies contain dataset rows, system
    prompts, model weights metadata — basically the full prompt surface.
    """
    # Phase D — every read is scoped by tenant + user (admin sees tenant-wide).
    from apps.api.services.identity import current_identity
    from apps.api.services.scoping import scope_query

    identity = current_identity(request)
    stmt = scope_query(select(HermesTrace), identity, HermesTrace)
    if source_like:
        stmt = stmt.where(HermesTrace.source.contains(source_like))  # type: ignore[attr-defined]
    if tenant_id:
        # Admins can narrow further by tenant_id; non-admins are already
        # restricted to their own tenant by scope_query, so this filter
        # can only ever narrow (never widen) — safe to honour.
        stmt = stmt.where(HermesTrace.tenant_id == tenant_id)
    if skill:
        stmt = stmt.where(HermesTrace.skill_name.in_(skill))  # type: ignore[union-attr]
    if status == "success":
        stmt = stmt.where(HermesTrace.success.is_(True))  # type: ignore[attr-defined]
    elif status == "error":
        stmt = stmt.where(HermesTrace.success.is_(False))  # type: ignore[attr-defined]
    elif status is not None:
        raise HTTPException(400, "status must be 'success' or 'error'")
    if since:
        try:
            stmt = stmt.where(HermesTrace.created_at >= datetime.fromisoformat(since))
        except ValueError as e:
            raise HTTPException(400, f"invalid `since`: {e}") from e
    if until:
        try:
            stmt = stmt.where(HermesTrace.created_at <= datetime.fromisoformat(until))
        except ValueError as e:
            raise HTTPException(400, f"invalid `until`: {e}") from e
    if min_duration_ms is not None:
        stmt = stmt.where(HermesTrace.duration_ms >= min_duration_ms)
    if run_id is not None:
        stmt = stmt.where(HermesTrace.run_id == run_id)
    if session_id is not None:
        stmt = stmt.where(HermesTrace.session_id == session_id)
    if kind is not None:
        if kind not in {"agent", "skill", "tool"}:
            raise HTTPException(400, "kind must be one of: agent, skill, tool")
        stmt = stmt.where(HermesTrace.kind == kind)
    if agent_run_id is not None:
        stmt = stmt.where(HermesTrace.agent_run_id == agent_run_id)

    if group_by == "trace":
        # Collect every span involved in the matching traces, then group
        # client-side. The `limit` applies to *trees*, not rows, so we
        # first pick the matching trace_ids, then pull every row for
        # those traces (in any order, sorted on the way out).
        # Fetch the rows that pass every other filter, then collapse to the
        # distinct trace_ids ordered by newest first. SQLModel's exec()
        # returns scalar values for single-column selects in some configs
        # and tuples in others, so we drop down to .all() with the
        # original select() and extract trace_id on the Python side — this
        # also keeps the existing filter logic identical between modes.
        seen_trace_ids: list[str] = []
        seen: set[str] = set()
        scored_rows = list(db.exec(stmt.order_by(desc(HermesTrace.created_at))).all())
        for r in scored_rows:
            if r.trace_id and r.trace_id not in seen:
                seen.add(r.trace_id)
                seen_trace_ids.append(r.trace_id)
                if len(seen_trace_ids) >= limit:
                    break
        trace_ids = seen_trace_ids
        if not trace_ids:
            return []
        # Phase D — re-scope the trace-tree fetch too. The trace_ids we
        # collected are already filtered through the caller's lens, so
        # this is belt-and-braces.
        all_rows = list(
            db.exec(
                scope_query(
                    select(HermesTrace).where(HermesTrace.trace_id.in_(trace_ids))  # type: ignore[union-attr]
                    .order_by(HermesTrace.created_at),
                    identity,
                    HermesTrace,
                )
            ).all()
        )
        return _build_trees(all_rows)

    if group_by not in (None, "", "none"):
        raise HTTPException(400, "group_by must be 'trace' or omitted")

    stmt = stmt.order_by(desc(HermesTrace.created_at)).limit(limit)
    rows = list(db.exec(stmt).all())
    prev_hashes = _previous_skill_hashes(db, [r.id for r in rows if r.id is not None])
    return [_row_to_trace(r, prev_hashes.get(r.id or -1)) for r in rows]


def _build_trees(rows: list[HermesTrace]) -> list[TraceTreeRow]:
    """Group rows by ``trace_id`` and pick a root span per group.

    The root is the row with ``parent_span_id IS NULL`` (the agent
    span); if none exists, the oldest row wins so legacy data still
    renders. Non-root rows become ``children``, sorted by created_at.
    """
    by_trace: dict[str, list[HermesTrace]] = {}
    for r in rows:
        tid = r.trace_id or ""
        by_trace.setdefault(tid, []).append(r)

    out: list[TraceTreeRow] = []
    for tid, group in by_trace.items():
        group_sorted = sorted(group, key=lambda x: x.created_at)
        roots = [r for r in group_sorted if r.parent_span_id is None]
        root = roots[0] if roots else group_sorted[0]
        children = [r for r in group_sorted if r is not root]
        root_row = _row_to_trace(root, None)
        out.append(
            TraceTreeRow(
                **root_row.model_dump(),
                children=[_row_to_trace(c, None) for c in children],
            )
        )
    # Order trees by their root's created_at descending (newest trace first).
    out.sort(key=lambda t: t.created_at, reverse=True)
    return out


@router.get("/{trace_id}", response_model=TraceRow)
@requires("read", "setting")
def get_trace(trace_id: int, request: Request, db: SessionDep) -> TraceRow:
    from apps.api.services.identity import current_identity
    from apps.api.services.scoping import scope_query

    identity = current_identity(request)
    row = db.exec(
        scope_query(
            select(HermesTrace).where(HermesTrace.id == trace_id), identity, HermesTrace
        )
    ).first()
    if not row:
        raise HTTPException(404, f"Trace {trace_id} not found")
    prev_hashes = _previous_skill_hashes(db, [trace_id])
    return _row_to_trace(row, prev_hashes.get(trace_id))


@router.delete("", status_code=204)
@requires("delete", "setting")
def clear_traces(request: Request, db: SessionDep) -> None:
    """Drop all trace rows. Admin-only."""
    db.exec(select(HermesTrace)).all()  # ensure mapping is loaded
    from sqlalchemy import delete as _delete

    db.exec(_delete(HermesTrace))  # type: ignore[arg-type]
    db.commit()


@router.get("/sources/list")
@requires("read", "setting")
def list_sources(request: Request, db: SessionDep) -> dict[str, Any]:
    """Distinct `source` strings + counts for the filter dropdown."""
    from apps.api.services.identity import current_identity
    from apps.api.services.scoping import scope_query

    identity = current_identity(request)
    rows = db.exec(
        scope_query(select(HermesTrace.source), identity, HermesTrace)
    ).all()
    counts: dict[str, int] = {}
    for s in rows:
        counts[s] = counts.get(s, 0) + 1
    return {"sources": [{"source": k, "count": v} for k, v in sorted(counts.items())]}


def _p95(values: list[int]) -> int:
    """Cheap p95 — sort + index. Trace volume is bounded at 500 rows so the
    cost is negligible; switching to ``statistics.quantiles`` would also work."""
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, round(0.95 * (len(s) - 1)))
    return s[idx]


@router.get("/skills/summary", response_model=list[SkillSummaryRow])
@requires("read", "setting")
def list_skill_summary(request: Request, db: SessionDep) -> list[SkillSummaryRow]:
    """Per-skill rollup for the Traces tab's left "Skill Activity" panel.

    Aggregates the trace table into one row per skill: calls, errors,
    avg + p95 latency, first/last seen, current hash, and the number of
    hash transitions observed (the "skill content changed N times" badge).
    """
    # ``hermes_traces`` is trimmed to the most-recent 500 rows by the
    # trim_old_traces job, so a single ordered scan + in-Python aggregation
    # is fast enough and sidesteps SQLAlchemy/SQLModel typing noise around
    # aggregate window functions.
    from apps.api.services.identity import current_identity
    from apps.api.services.scoping import scope_query

    identity = current_identity(request)
    all_rows = db.exec(
        scope_query(
            select(HermesTrace)
            .where(HermesTrace.skill_name.is_not(None))  # type: ignore[union-attr]
            .order_by(HermesTrace.created_at),  # type: ignore[arg-type]
            identity,
            HermesTrace,
        )
    ).all()

    grouped: dict[str, list[HermesTrace]] = {}
    for r in all_rows:
        if r.skill_name is None:
            continue
        grouped.setdefault(r.skill_name, []).append(r)

    out: list[SkillSummaryRow] = []
    for skill_name, rows in grouped.items():
        calls = len(rows)
        errors = sum(1 for r in rows if not r.success)
        durations = [r.duration_ms for r in rows]
        change_count = 0
        last_hash: str | None = None
        for r in rows:
            if r.skill_sha256 is not None and last_hash is not None and r.skill_sha256 != last_hash:
                change_count += 1
            if r.skill_sha256 is not None:
                last_hash = r.skill_sha256
        out.append(
            SkillSummaryRow(
                skill_name=skill_name,
                calls=calls,
                errors=errors,
                avg_duration_ms=sum(durations) // calls if calls else 0,
                p95_duration_ms=_p95(durations),
                first_seen=rows[0].created_at.isoformat(),
                last_seen=rows[-1].created_at.isoformat(),
                current_sha256=last_hash,
                change_count=change_count,
            )
        )
    out.sort(key=lambda r: r.last_seen, reverse=True)
    return out


@router.get("/by-trace/{trace_id}", response_model=TraceTreeRow)
@requires("read", "setting")
def get_trace_tree(
    trace_id: str,
    request: Request,
    db: SessionDep,
) -> TraceTreeRow:
    """Return the full tree for a single trace_id (root + children).

    404 when no rows match. Phase B — used by the Traces tab's
    expand-on-row action.
    """
    from apps.api.services.identity import current_identity
    from apps.api.services.scoping import scope_query

    identity = current_identity(request)
    rows = list(
        db.exec(
            scope_query(
                select(HermesTrace)
                .where(HermesTrace.trace_id == trace_id)
                .order_by(HermesTrace.created_at),
                identity,
                HermesTrace,
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"trace_id {trace_id!r} not found")
    trees = _build_trees(rows)
    return trees[0]
