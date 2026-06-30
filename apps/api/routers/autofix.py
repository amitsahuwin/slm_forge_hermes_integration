"""PR-A — read-only Auto-Fixes admin endpoints.

Reports + (eventually) auto-fix attempts. All endpoints are admin-gated
via the existing ``@requires`` decorator. Write endpoints land in PR-B.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, desc, func, select

from apps.api.middleware.auth import requires
from apps.api.models.autofix import AutoFixAttempt, AutoFixStatus
from apps.api.services.db import get_session
from apps.api.services.identity import current_identity
from apps.api.services.scoping import scope_query

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


class AutoFixRow(BaseModel):
    id: int
    fingerprint: str
    mode: str
    source: str
    error_type: str
    error_message: str
    file_target: str | None
    branch: str | None
    status: str
    attempt_count: int
    issue_url: str | None
    pr_url: str | None
    occurrences_in_window: int
    correlation_request_id: str | None
    correlation_run_id: str | None
    correlation_session_id: str | None
    tenant_id: str
    created_at: str
    completed_at: str | None


def _row(a: AutoFixAttempt) -> AutoFixRow:
    return AutoFixRow(
        id=a.id or 0,
        fingerprint=a.fingerprint,
        mode=a.mode,
        source=a.source,
        error_type=a.error_type,
        error_message=a.error_message,
        file_target=a.file_target,
        branch=a.branch,
        status=a.status,
        attempt_count=a.attempt_count,
        issue_url=a.issue_url,
        pr_url=a.pr_url,
        occurrences_in_window=a.occurrences_in_window,
        correlation_request_id=a.correlation_request_id,
        correlation_run_id=a.correlation_run_id,
        correlation_session_id=a.correlation_session_id,
        tenant_id=a.tenant_id,
        created_at=a.created_at.isoformat(),
        completed_at=a.completed_at.isoformat() if a.completed_at else None,
    )


@router.get("/attempts", response_model=list[AutoFixRow])
@requires("read", "setting")
def list_attempts(
    request: Request,
    db: SessionDep,
    status: str | None = Query(None),
    fingerprint: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[AutoFixRow]:
    """Newest-first list of captured errors / auto-fix attempts."""
    # Phase D — tenant/user-scoped. Admins see tenant-wide; non-admins
    # see only their own. Cross-tenant access is blocked even for admins.
    identity = current_identity(request)
    stmt = scope_query(select(AutoFixAttempt), identity, AutoFixAttempt)
    if status:
        stmt = stmt.where(AutoFixAttempt.status == status)
    if fingerprint:
        stmt = stmt.where(AutoFixAttempt.fingerprint == fingerprint)
    stmt = stmt.order_by(desc(AutoFixAttempt.created_at)).offset(offset).limit(limit)
    return [_row(a) for a in db.exec(stmt).all()]


class AutoFixDetail(AutoFixRow):
    """Detailed view — includes the (potentially large) diff."""

    diff: str | None
    test_path: str | None


@router.get("/attempts/{attempt_id}", response_model=AutoFixDetail)
@requires("read", "setting")
def get_attempt(attempt_id: int, request: Request, db: SessionDep) -> AutoFixDetail:
    identity = current_identity(request)
    row = db.exec(
        scope_query(
            select(AutoFixAttempt).where(AutoFixAttempt.id == attempt_id),
            identity,
            AutoFixAttempt,
        )
    ).first()
    if row is None:
        raise HTTPException(404, f"AutoFixAttempt {attempt_id} not found")
    base = _row(row).model_dump()
    return AutoFixDetail(**base, diff=row.diff, test_path=row.test_path)


class AbandonResponse(BaseModel):
    id: int
    status: str
    completed_at: str


@router.post("/attempts/{attempt_id}/abandon", response_model=AbandonResponse)
@requires("delete", "setting")
def abandon_attempt(
    attempt_id: int, request: Request, db: SessionDep
) -> AbandonResponse:
    """Mark an attempt as ``rejected`` so it doesn't get auto-retried.

    Useful when ops decides not to act on a particular error class. The
    rejection is permanent until a manual DB write reverses it.
    """
    identity = current_identity(request)
    row = db.exec(
        scope_query(
            select(AutoFixAttempt).where(AutoFixAttempt.id == attempt_id),
            identity,
            AutoFixAttempt,
        )
    ).first()
    if row is None:
        raise HTTPException(404, f"AutoFixAttempt {attempt_id} not found")
    row.status = AutoFixStatus.REJECTED.value
    row.completed_at = datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return AbandonResponse(
        id=row.id or 0,
        status=row.status,
        completed_at=row.completed_at.isoformat() if row.completed_at else "",
    )


class AutoFixStats(BaseModel):
    total: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    by_mode: dict[str, int]


@router.get("/stats", response_model=AutoFixStats)
@requires("read", "setting")
def stats(request: Request, db: SessionDep) -> AutoFixStats:
    identity = current_identity(request)
    total = db.exec(
        scope_query(select(func.count(AutoFixAttempt.id)), identity, AutoFixAttempt)  # type: ignore[arg-type]
    ).one()

    def _group(col: Any) -> dict[str, int]:
        rows = db.exec(
            scope_query(
                select(col, func.count(AutoFixAttempt.id)).group_by(col),  # type: ignore[arg-type]
                identity,
                AutoFixAttempt,
            )
        ).all()
        return {str(k): int(v) for k, v in rows}

    return AutoFixStats(
        total=int(total),
        by_status=_group(AutoFixAttempt.status),
        by_source=_group(AutoFixAttempt.source),
        by_mode=_group(AutoFixAttempt.mode),
    )
