"""Run management + live metric streaming."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.middleware.auth import requires
from apps.api.models.metric import Metric
from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.services.db import get_session
from apps.api.services.model_catalog import validate_run_request

router = APIRouter()


class RunCreate(BaseModel):
    dataset: str
    # Phase P — default switched off the broken gemma-3n bf16 checkpoint
    # to the catalog default (kept in sync by tests/api/test_run_validation.py).
    base_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit"
    method: RunMethod = RunMethod.LORA
    iters: int = 200
    batch_size: int = 4
    learning_rate: float = 1.0e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    grad_checkpoint: bool = False
    seed: int = 0
    # Phase O — backend selector; immutable after creation (not in RunPatch).
    trainer_backend: str = "mlx"


class RunPatch(BaseModel):
    status: RunStatus | None = None
    error_message: str | None = None
    adapter_path: str | None = None
    final_train_loss: float | None = None
    final_val_loss: float | None = None
    # Phase 2 ratchet fields:
    session_id: int | None = None
    parent_run_id: int | None = None
    iteration_number: int | None = None
    was_accepted: bool | None = None
    mutation_reasoning: str | None = None
    canary_loss: float | None = None


class MetricCreate(BaseModel):
    step: int
    name: str
    value: float


SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=Run)
def create_run(payload: RunCreate, session: SessionDep) -> Run:
    # Phase P — catalog enforcement (disable via SLM_FORGE_ENFORCE_CATALOG=false).
    error = validate_run_request(payload.base_model, payload.trainer_backend)
    if error is not None:
        raise HTTPException(422, error)
    run = Run(**payload.model_dump())
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("", response_model=list[Run])
def list_runs(
    session: SessionDep,
    status: RunStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[Run]:
    stmt = select(Run).order_by(desc(Run.created_at)).limit(limit)
    if status is not None:
        stmt = (
            select(Run)
            .where(Run.status == status)
            .order_by(desc(Run.created_at))
            .limit(limit)
        )
    return list(session.exec(stmt).all())


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: int, session: SessionDep) -> Run:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.patch("/{run_id}", response_model=Run)
def patch_run(run_id: int, payload: RunPatch, session: SessionDep) -> Run:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(run, k, v)
    if payload.status == RunStatus.RUNNING and run.started_at is None:
        run.started_at = datetime.now(UTC)
    if payload.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.completed_at = datetime.now(UTC)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@router.get("/{run_id}/metrics", response_model=list[Metric])
def list_metrics(run_id: int, session: SessionDep) -> list[Metric]:
    if not session.get(Run, run_id):
        raise HTTPException(404, "Run not found")
    stmt = select(Metric).where(Metric.run_id == run_id).order_by(Metric.step, Metric.id)
    return list(session.exec(stmt).all())


@router.post("/{run_id}/metrics", response_model=Metric)
def post_metric(run_id: int, payload: MetricCreate, session: SessionDep) -> Metric:
    if not session.get(Run, run_id):
        raise HTTPException(404, "Run not found")
    m = Metric(run_id=run_id, **payload.model_dump())
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@router.get("/{run_id}/stream")
async def stream_run(run_id: int) -> EventSourceResponse:
    async def event_gen() -> AsyncGenerator[dict[str, str], None]:
        last_metric_id = 0
        last_status: str | None = None
        terminal = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}
        from sqlmodel import Session as _Session

        from apps.api.services.db import engine

        while True:
            with _Session(engine) as s:
                run = s.get(Run, run_id)
                if not run:
                    yield {"event": "error", "data": json.dumps({"message": "Run not found"})}
                    return

                if run.status.value != last_status:
                    last_status = run.status.value
                    yield {"event": "status", "data": json.dumps({"status": run.status.value, "run_id": run.id})}

                new_metrics = s.exec(
                    select(Metric)
                    .where(Metric.run_id == run_id, Metric.id > last_metric_id)
                    .order_by(Metric.id)
                ).all()

                for m in new_metrics:
                    last_metric_id = m.id or last_metric_id
                    yield {
                        "event": "metric",
                        "data": json.dumps({
                            "step": m.step, "name": m.name, "value": m.value,
                            "recorded_at": m.recorded_at.isoformat(),
                        }),
                    }

                if run.status.value in terminal:
                    yield {"event": "done", "data": json.dumps({"status": run.status.value})}
                    return

            await asyncio.sleep(0.75)

    return EventSourceResponse(event_gen())


@router.delete("/{run_id}", status_code=204)
@requires("delete", "run")
def delete_run(run_id: int, request: Request, session: SessionDep) -> None:
    """Delete a run and its metrics. Blocks if the run has exports."""
    import shutil
    from pathlib import Path

    from apps.api.models.export import Export

    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Block if exports exist
    exp = session.exec(select(Export).where(Export.run_id == run_id).limit(1)).first()
    if exp:
        raise HTTPException(
            409,
            f"Run #{run_id} has export #{exp.id}. Delete the export first.",
        )

    # Delete metrics (cascade)
    metrics_to_delete = session.exec(select(Metric).where(Metric.run_id == run_id)).all()
    for m in metrics_to_delete:
        session.delete(m)

    session.delete(run)
    session.commit()

    # Delete on-disk artifacts
    run_dir = Path("/app/runs") / str(run_id)
    if run_dir.exists():
        try:
            shutil.rmtree(run_dir)
        except OSError:
            pass
