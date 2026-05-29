"""Sessions API — autoresearch orchestration."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from apps.api.models.run import Run, RunMethod
from apps.api.models.session import SessionStatus, TargetMetric, TrainingSession
from apps.api.services.db import get_session

router = APIRouter()


class SessionCreate(BaseModel):
    name: str
    dataset: str
    base_model: str = "mlx-community/gemma-3n-E2B-it-bf16"
    method: RunMethod = RunMethod.LORA
    iters: int = 100
    batch_size: int = 4
    learning_rate: float = 1e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    max_rounds: int = 8
    plateau_patience: int = 3
    min_delta: float = 0.005
    target_metric: TargetMetric = TargetMetric.VAL_LOSS
    canary_drift_threshold: float = 0.3


class SessionPatch(BaseModel):
    status: SessionStatus | None = None
    current_round: int | None = None
    best_run_id: int | None = None
    best_metric_value: float | None = None
    error_message: str | None = None


SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=TrainingSession)
def create_session(payload: SessionCreate, db: SessionDep) -> TrainingSession:
    s = TrainingSession(**payload.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("", response_model=list[TrainingSession])
def list_sessions(
    db: SessionDep,
    status: SessionStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[TrainingSession]:
    stmt = select(TrainingSession).order_by(desc(TrainingSession.created_at)).limit(limit)
    if status is not None:
        stmt = (
            select(TrainingSession)
            .where(TrainingSession.status == status)
            .order_by(desc(TrainingSession.created_at))
            .limit(limit)
        )
    return list(db.exec(stmt).all())


@router.get("/{sid}", response_model=TrainingSession)
def get_session_(sid: int, db: SessionDep) -> TrainingSession:
    s = db.get(TrainingSession, sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.patch("/{sid}", response_model=TrainingSession)
def patch_session(sid: int, payload: SessionPatch, db: SessionDep) -> TrainingSession:
    s = db.get(TrainingSession, sid)
    if not s:
        raise HTTPException(404, "Session not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    if payload.status == SessionStatus.RUNNING and s.started_at is None:
        s.started_at = datetime.now(UTC)
    if payload.status in {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED}:
        s.completed_at = datetime.now(UTC)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/{sid}/iterations", response_model=list[Run])
def list_iterations(sid: int, db: SessionDep) -> list[Run]:
    if not db.get(TrainingSession, sid):
        raise HTTPException(404, "Session not found")
    return list(
        db.exec(
            select(Run).where(Run.session_id == sid).order_by(Run.iteration_number)
        ).all()
    )
