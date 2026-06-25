"""Sessions API — autoresearch orchestration."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from apps.api.middleware.auth import requires
from apps.api.models.run import Run, RunMethod
from apps.api.models.session import SessionStatus, TargetMetric, TrainingSession
from apps.api.services.db import get_session
from apps.api.services.model_catalog import validate_run_request

router = APIRouter()


class SessionCreate(BaseModel):
    name: str
    dataset: str
    base_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit"
    # Phase U — backend pinned for every iteration; validated against the catalog
    # exactly like RunCreate so a bad model/backend combo fails at create time.
    trainer_backend: str = "mlx"
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
@requires("create", "experiment")
def create_session(
    payload: SessionCreate, request: Request, db: SessionDep
) -> TrainingSession:
    # Phase U — catalog enforcement (disable via SLM_FORGE_ENFORCE_CATALOG=false),
    # mirroring create_run so an experiment can't be queued for a backend/model
    # combo no worker can ever claim.
    error = validate_run_request(payload.base_model, payload.trainer_backend)
    if error is not None:
        raise HTTPException(422, error)
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


# Config fields cloned by ``rerun_session``. Anything not listed here
# (status, progress, results, timestamps) is initialised to its model
# default so the new session reads as a fresh queued experiment.
_RERUN_CLONED_FIELDS = (
    "dataset",
    "base_model",
    "trainer_backend",
    "method",
    "iters",
    "batch_size",
    "learning_rate",
    "num_layers",
    "max_seq_length",
    "max_rounds",
    "plateau_patience",
    "min_delta",
    "target_metric",
    "canary_drift_threshold",
)


@router.post("/{sid}/rerun", response_model=TrainingSession)
@requires("create", "experiment")
def rerun_session(
    sid: int, request: Request, db: SessionDep,
) -> TrainingSession:
    """Clone a session's config into a new ``queued`` session.

    The source row is never mutated — its history (runs, metrics, best
    pointer, error message) stays intact. The ratchet worker picks up
    the new session via its normal queue poll.
    """
    src = db.get(TrainingSession, sid)
    if not src:
        raise HTTPException(404, "Session not found")
    payload = {field: getattr(src, field) for field in _RERUN_CLONED_FIELDS}
    new = TrainingSession(
        name=f"{src.name} (rerun)",
        status=SessionStatus.QUEUED,
        **payload,
    )
    db.add(new)
    db.commit()
    db.refresh(new)
    return new


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


@router.delete("/{sid}", status_code=204)
def delete_session(sid: int, db: SessionDep) -> None:
    """Delete a session and ALL its child runs (cascading). Blocks if any child run has exports."""
    import shutil
    from pathlib import Path

    from apps.api.models.export import Export
    from apps.api.models.metric import Metric

    s = db.get(TrainingSession, sid)
    if not s:
        raise HTTPException(404, "Session not found")

    child_runs = list(db.exec(select(Run).where(Run.session_id == sid)).all())
    child_ids = [r.id for r in child_runs]

    # Block if any child has exports
    if child_ids:
        exp = db.exec(
            select(Export).where(Export.run_id.in_(child_ids)).limit(1)
        ).first()
        if exp:
            raise HTTPException(
                409,
                f"Session #{sid} has run #{exp.run_id} with export #{exp.id}. "
                "Delete that export first.",
            )

    # Cascade delete metrics → runs → session
    for r in child_runs:
        for m in list(db.exec(select(Metric).where(Metric.run_id == r.id)).all()):
            db.delete(m)
        db.delete(r)
        run_dir = Path("/app/runs") / str(r.id)
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except OSError:
                pass

    db.delete(s)
    db.commit()
