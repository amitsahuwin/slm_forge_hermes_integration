"""Multi-step Hermes agents (Phase N.3) — REST + SSE endpoints.

Wraps ``packages.agents.runner`` so the React UI can:
  1. List available agents (``GET /agents``)
  2. Kick off an agent (``POST /agents/{name}/run``) and stream live events
  3. Or invoke synchronously (``POST /agents/{name}/run-sync``) for short flows

Each agent's input schema is named after the agent:
  • experiment_recommender → ``ExperimentRecommenderIn``
  • optimization_coach     → ``OptimizationCoachIn``
  • evaluation_designer    → ``EvaluationDesignerIn``
  • incident_responder     → ``IncidentResponderIn``
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.run import Run, RunStatus
from apps.api.models.session import TrainingSession
from apps.api.routers.hermes import _tail_training_log
from apps.api.services.db import get_session
from packages.agents.runner import stream_agent

log = logging.getLogger("api.agents")
router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


# ─── Agent catalogue (for the /agents listing) ───────────────────────────


_CATALOGUE = [
    {
        "name": "experiment_recommender",
        "title": "Experiment Recommender",
        "blurb": (
            "End-to-end plan: data quality review → base-model pick → "
            "method + hyperparam recommendation."
        ),
        "inputs": ["dataset", "task_description", "target_device?"],
    },
    {
        "name": "optimization_coach",
        "title": "Optimization Coach",
        "blurb": (
            "Looks at a session's iteration history + canary drift and "
            "recommends continue / pivot / stop."
        ),
        "inputs": ["session_id"],
    },
    {
        "name": "evaluation_designer",
        "title": "Evaluation Designer",
        "blurb": (
            "Builds a 5-record canary set plus success criteria and 5 "
            "benchmark questions for a dataset."
        ),
        "inputs": ["dataset"],
    },
    {
        "name": "incident_responder",
        "title": "Incident Responder",
        "blurb": (
            "Generates a markdown post-mortem for a failed run and decides "
            "if it's safe to re-queue."
        ),
        "inputs": ["run_id"],
    },
]


@router.get("/")
def list_agents() -> list[dict[str, Any]]:
    return _CATALOGUE


# ─── Input schemas ───────────────────────────────────────────────────────


class ExperimentRecommenderIn(BaseModel):
    dataset: str
    task_description: str
    target_device: str = "mac_desktop"


class OptimizationCoachIn(BaseModel):
    session_id: int


class EvaluationDesignerIn(BaseModel):
    dataset: str


class IncidentResponderIn(BaseModel):
    run_id: int


# ─── Input → runner-args adapter ─────────────────────────────────────────


def _prepare_args(name: str, payload: dict[str, Any], db: Session) -> tuple[tuple, dict]:
    """Translate the REST payload into the positional/keyword args the
    underlying ``run_<agent>`` function expects. Also enriches inputs with
    DB-side data (iteration history, run config, log tail) so the agent
    receives everything it needs.
    """
    if name == "experiment_recommender":
        return (
            (payload["dataset"], payload["task_description"]),
            {"target_device": payload.get("target_device", "mac_desktop")},
        )
    if name == "optimization_coach":
        sid = int(payload["session_id"])
        sess = db.get(TrainingSession, sid)
        if not sess:
            raise HTTPException(404, f"Session {sid} not found")
        runs = db.exec(
            select(Run).where(Run.session_id == sid).order_by(Run.iteration_number)
        ).all()
        iterations = [
            {
                "iter": r.iteration_number,
                "method": r.method.value,
                "lr": r.learning_rate,
                "batch_size": r.batch_size,
                "num_layers": r.num_layers,
                "val_loss": r.final_val_loss,
                "canary_loss": r.canary_loss,
                "was_accepted": r.was_accepted,
            }
            for r in runs
            if r.iteration_number is not None
        ]
        return (
            (sid, iterations, sess.canary_drift_threshold, sess.dataset),
            {},
        )
    if name == "evaluation_designer":
        return ((payload["dataset"],), {})
    if name == "incident_responder":
        rid = int(payload["run_id"])
        run = db.get(Run, rid)
        if not run:
            raise HTTPException(404, f"Run {rid} not found")
        run_data = {
            "status": run.status.value,
            "method": run.method.value,
            "base_model": run.base_model,
            "batch_size": run.batch_size,
            "num_layers": run.num_layers,
            "max_seq_length": run.max_seq_length,
            "iters": run.iters,
            "learning_rate": run.learning_rate,
            "error_message": run.error_message or "",
        }
        return ((rid, run_data, _tail_training_log(rid, 80)), {})
    raise HTTPException(404, f"Unknown agent {name!r}")


# ─── Endpoints ───────────────────────────────────────────────────────────


_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "experiment_recommender": ExperimentRecommenderIn,
    "optimization_coach": OptimizationCoachIn,
    "evaluation_designer": EvaluationDesignerIn,
    "incident_responder": IncidentResponderIn,
}


def _validate_payload(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the per-agent payload and surface a UI-friendly 422.

    On failure, ``detail`` is a dict carrying:
      - ``agent``: the agent name,
      - ``missing_fields``: the list of fields Pydantic flagged (works for
        both *missing* and *bad type* errors),
      - ``hint``: a one-line nudge that pairs with the schema.
    The frontend (Agents.tsx) renders this directly as an inline banner.
    """
    schema = _INPUT_MODELS.get(name)
    if schema is None:
        raise HTTPException(404, f"Unknown agent {name!r}")
    try:
        return schema(**payload).model_dump()
    except ValidationError as e:
        bad_fields: list[str] = []
        for err in e.errors():
            loc = err.get("loc") or ()
            if loc:
                bad_fields.append(str(loc[0]))
        # Preserve order, dedupe.
        seen: set[str] = set()
        missing_fields = [f for f in bad_fields if not (f in seen or seen.add(f))]
        expected = list(schema.model_fields.keys())
        hint = f"{name!r} expects {expected}; check missing_fields."
        raise HTTPException(
            status_code=422,
            detail={
                "agent": name,
                "missing_fields": missing_fields,
                "hint": hint,
            },
        ) from e


@router.post("/{name}/run-sync")
def run_sync(name: str, payload: dict[str, Any], db: SessionDep) -> dict[str, Any]:
    """Run an agent synchronously and return the full state when done.

    Easier than SSE for chat-tool style calls; uses no streaming. Note:
    deep agents can take ~30s+ since they make multiple Ollama calls.
    """
    clean = _validate_payload(name, payload)
    args, kwargs = _prepare_args(name, clean, db)
    from packages.agents import runner as _r

    runners = {
        "experiment_recommender": _r.run_experiment_recommender,
        "optimization_coach": _r.run_optimization_coach,
        "evaluation_designer": _r.run_evaluation_designer,
        "incident_responder": _r.run_incident_responder,
    }
    try:
        result = runners[name](*args, **kwargs)
    except Exception as e:
        log.exception("agent %s sync run failed", name)
        raise HTTPException(500, f"Agent failed: {e}") from e
    return {
        "agent": name,
        "recommendation": result.get("recommendation", {}),
        "steps": result.get("steps", {}),
        "log": result.get("log", []),
    }


@router.post("/{name}/run")
async def run_stream(name: str, payload: dict[str, Any], db: SessionDep) -> EventSourceResponse:
    """Kick off an agent and stream live progress as SSE.

    Events emitted:
      • ``stage`` — every node transition (`*_start`, `*_done`)
      • ``complete`` — final recommendation + all step outputs
      • ``error`` — if anything blows up
    """
    clean = _validate_payload(name, payload)
    args, kwargs = _prepare_args(name, clean, db)

    async def gen() -> AsyncGenerator[dict[str, str], None]:
        async for ev in stream_agent(name, *args, **kwargs):
            yield {
                "event": ev.get("stage", "stage"),
                "data": json.dumps(ev, default=str),
            }

    return EventSourceResponse(gen())


# ─── Incident watchdog: GET helper the Dashboard polls ───────────────────


@router.get("/incidents/recent")
def recent_incidents(db: SessionDep, limit: int = 5) -> list[dict[str, Any]]:
    """Return recently failed runs that haven't been post-mortemed yet.

    The Dashboard polls this for the "needs attention" notice (Phase N.3
    incident_responder integration).
    """
    from pathlib import Path as _P

    rows = db.exec(
        select(Run)
        .where(Run.status == RunStatus.FAILED)
        .order_by(Run.id.desc())  # type: ignore[attr-defined]
        .limit(max(1, min(limit, 20)))
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        candidates = [
            _P("/app/runs") / str(r.id) / "post_mortem.md",
            _P(__file__).resolve().parents[3] / "runs" / str(r.id) / "post_mortem.md",
        ]
        has_pm = any(p.exists() for p in candidates)
        out.append(
            {
                "run_id": r.id,
                "error_message": (r.error_message or "")[:160],
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "has_post_mortem": has_pm,
            }
        )
    return out
