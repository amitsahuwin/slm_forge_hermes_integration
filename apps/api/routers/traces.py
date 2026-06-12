"""Hermes / Ollama request-response trace inspector.

Admin-only by policy. Returns recent rows from ``hermes_traces`` so the
"Traces" tab can render the JSON bodies side-by-side without scraping
gigabytes of structured logs.
"""
from __future__ import annotations

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


@router.get("", response_model=list[TraceRow])
@requires("read", "setting")
def list_traces(
    request: Request,
    db: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    source_like: str | None = Query(None, description="substring filter on `source`"),
) -> list[TraceRow]:
    """Return the most recent traces, newest first.

    Locked to admin (and any other role granted ``read`` on ``setting``)
    because the request/response bodies contain dataset rows, system
    prompts, model weights metadata — basically the full prompt surface.
    """
    stmt = select(HermesTrace).order_by(desc(HermesTrace.created_at)).limit(limit)
    if source_like:
        stmt = (
            select(HermesTrace)
            .where(HermesTrace.source.contains(source_like))  # type: ignore[attr-defined]
            .order_by(desc(HermesTrace.created_at))
            .limit(limit)
        )
    rows = db.exec(stmt).all()
    return [
        TraceRow(
            id=r.id or 0,
            created_at=r.created_at.isoformat(),
            source=r.source,
            model=r.model,
            duration_ms=r.duration_ms,
            error=r.error,
            request_body=r.request_body,
            response_body=r.response_body,
        )
        for r in rows
    ]


@router.get("/{trace_id}", response_model=TraceRow)
@requires("read", "setting")
def get_trace(trace_id: int, request: Request, db: SessionDep) -> TraceRow:
    row = db.get(HermesTrace, trace_id)
    if not row:
        raise HTTPException(404, f"Trace {trace_id} not found")
    return TraceRow(
        id=row.id or 0,
        created_at=row.created_at.isoformat(),
        source=row.source,
        model=row.model,
        duration_ms=row.duration_ms,
        error=row.error,
        request_body=row.request_body,
        response_body=row.response_body,
    )


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
