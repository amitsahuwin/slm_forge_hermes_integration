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


@router.get("", response_model=list[TraceRow])
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
) -> list[TraceRow]:
    """Return the most recent traces, newest first.

    Locked to admin (and any other role granted ``read`` on ``setting``)
    because the request/response bodies contain dataset rows, system
    prompts, model weights metadata — basically the full prompt surface.
    """
    stmt = select(HermesTrace)
    if source_like:
        stmt = stmt.where(HermesTrace.source.contains(source_like))  # type: ignore[attr-defined]
    if tenant_id:
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
    stmt = stmt.order_by(desc(HermesTrace.created_at)).limit(limit)

    rows = list(db.exec(stmt).all())
    prev_hashes = _previous_skill_hashes(db, [r.id for r in rows if r.id is not None])
    return [_row_to_trace(r, prev_hashes.get(r.id or -1)) for r in rows]


@router.get("/{trace_id}", response_model=TraceRow)
@requires("read", "setting")
def get_trace(trace_id: int, request: Request, db: SessionDep) -> TraceRow:
    row = db.get(HermesTrace, trace_id)
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
    rows = db.exec(select(HermesTrace.source)).all()
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
    all_rows = db.exec(
        select(HermesTrace)
        .where(HermesTrace.skill_name.is_not(None))  # type: ignore[union-attr]
        .order_by(HermesTrace.created_at)  # type: ignore[arg-type]
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
