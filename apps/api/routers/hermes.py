"""Hermes Agent status + skill-invocation endpoints.

Three concerns under this router:
  1. Status snapshot for the Dashboard card (`/status`, `/heartbeats`)
  2. Worker heartbeat ingestion (`/heartbeat`)
  3. Skill invocations — wire the dormant skills in ``.hermes-skills/`` into
     real UI buttons (Phase N.1):
       • `/select-method`       — recommend method + hyperparams for a task
       • `/diagnose-run/{rid}`  — explain a failed run (MPS OOM and similar)
       • `/analyze-drift/{sid}` — explain canary drift on an autoresearch session
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.models.heartbeat import WorkerHeartbeat
from apps.api.models.run import Run
from apps.api.models.session import TrainingSession
from apps.api.services.db import get_session
from packages.ratchet.hermes_bridge import (
    HERMES_MODEL,
    SKILLS_DIR,
    _call_ollama,
    healthcheck,
    load_skill,
)

log = logging.getLogger("api.hermes")

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

# Stale heartbeat threshold. After this, the worker is considered down.
WORKER_STALE_AFTER = timedelta(seconds=30)


# ─── Skills discovery ─────────────────────────────────────────────────────────

# In container: /app is the project root; on host it's the repo checkout.
_PROJECT_ROOT_CANDIDATES = [
    Path("/app"),
    Path(__file__).resolve().parents[3],
]


def _project_root() -> Path:
    for candidate in _PROJECT_ROOT_CANDIDATES:
        if (candidate / ".hermes-skills").exists():
            return candidate
    # Fallback: the parents[3] guess, even if the dir is missing
    return Path(__file__).resolve().parents[3]


def _list_skills() -> tuple[list[str], str]:
    """Return (skill_basenames, skills_dir_label).

    Looks in two places and dedupes:
      1. SKILLS_DIR (defaults to ~/.hermes/skills) — what the bridge actually loads
      2. Repo-level .hermes-skills/  — the source of truth in the repo
    """
    found: set[str] = set()
    label_parts: list[str] = []

    repo_skills = _project_root() / ".hermes-skills"
    if repo_skills.exists():
        label_parts.append(str(repo_skills))
        for p in repo_skills.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found.add(p.stem)

    if SKILLS_DIR.exists() and SKILLS_DIR != repo_skills:
        label_parts.append(str(SKILLS_DIR))
        for p in SKILLS_DIR.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found.add(p.stem)

    label = " | ".join(label_parts) if label_parts else str(SKILLS_DIR)
    return sorted(found), label


# ─── Schemas ──────────────────────────────────────────────────────────────────


class HermesStatus(BaseModel):
    ollama_reachable: bool
    model: str
    model_available: bool
    message: str
    worker_running: bool
    worker_last_seen: str | None
    skills_dir: str
    skills_installed: list[str]


class Heartbeat(BaseModel):
    worker: str
    version: str = ""


class HeartbeatAck(BaseModel):
    ok: bool
    worker: str
    received_at: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


def _aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (SQLite drops tz info on load)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("/status", response_model=HermesStatus)
def hermes_status(db: SessionDep) -> HermesStatus:
    """Compose a single-shot status snapshot for the dashboard card."""
    ok, message = healthcheck()
    # The bridge healthcheck only returns ok=True when BOTH Ollama is reachable
    # AND the model is pulled. Disambiguate using the message text.
    if ok:
        ollama_reachable = True
        model_available = True
    else:
        # "Ollama not reachable" ⇒ infra down; otherwise Ollama is up but model is missing.
        ollama_reachable = "not reachable" not in message.lower()
        model_available = False

    entry = db.get(WorkerHeartbeat, "ratchet")
    worker_running = False
    worker_last_seen: str | None = None
    if entry is not None:
        last_seen = _aware(entry.last_seen)
        worker_last_seen = last_seen.isoformat()
        worker_running = (datetime.now(UTC) - last_seen) < WORKER_STALE_AFTER

    skills, skills_dir_label = _list_skills()

    return HermesStatus(
        ollama_reachable=ollama_reachable,
        model=HERMES_MODEL,
        model_available=model_available,
        message=message,
        worker_running=worker_running,
        worker_last_seen=worker_last_seen,
        skills_dir=skills_dir_label,
        skills_installed=skills,
    )


@router.post("/heartbeat", response_model=HeartbeatAck)
def hermes_heartbeat(payload: Heartbeat, db: SessionDep) -> HeartbeatAck:
    """Workers POST here every N seconds to advertise liveness."""
    now = datetime.now(UTC)
    row = db.get(WorkerHeartbeat, payload.worker)
    if row is None:
        row = WorkerHeartbeat(
            worker=payload.worker,
            last_seen=now,
            version=payload.version or "unknown",
        )
    else:
        row.last_seen = now
        if payload.version:
            row.version = payload.version
    db.add(row)
    db.commit()
    return HeartbeatAck(ok=True, worker=payload.worker, received_at=now.isoformat())


@router.get("/heartbeats")
def list_heartbeats(db: SessionDep) -> dict[str, dict[str, str | bool]]:
    """Return every worker's last heartbeat. Dashboard reads this for the
    trainer/exporter tiles so they don't depend on log-line timestamps."""
    rows = db.exec(select(WorkerHeartbeat)).all()
    out: dict[str, dict[str, str | bool]] = {}
    now = datetime.now(UTC)
    for r in rows:
        last_seen = _aware(r.last_seen)
        out[r.worker] = {
            "last_seen": last_seen.isoformat(),
            "version": r.version,
            "running": (now - last_seen) < WORKER_STALE_AFTER,
        }
    return out


# ─── Skill invocations (Phase N.1) ───────────────────────────────────────────
#
# Each endpoint loads the corresponding ``.hermes-skills/<name>.md`` as the
# system prompt and asks Ollama via the existing ``hermes_bridge._call_ollama``.
# Output is returned as parsed JSON when possible, raw text otherwise — the
# UI handles both.


class SkillResponse(BaseModel):
    skill: str
    model: str
    parsed: dict[str, Any] | None
    raw: str
    elapsed_ms: int


def _run_skill(skill_name: str, user_payload: dict[str, Any]) -> SkillResponse:
    """Common entrypoint for skill invocations.

    Loads the named skill markdown, calls Ollama with it as system prompt and
    ``user_payload`` (as JSON) as the user message. Parses the response as
    JSON when possible. Raises HTTPException(503) if Ollama is unreachable.
    """
    skill = load_skill(skill_name)
    if skill is None:
        raise HTTPException(
            404,
            f"Skill {skill_name!r} not installed. Run `make hermes-install-skills` "
            "or verify the .hermes-skills/ directory is mounted.",
        )
    user_msg = json.dumps(user_payload, default=str)
    start = datetime.now(UTC)
    try:
        raw = _call_ollama(skill, user_msg, expect_json=True)
    except Exception as e:  # noqa: BLE001
        log.exception("skill %s failed", skill_name)
        raise HTTPException(
            503,
            f"Hermes/Ollama call failed: {e}. Check that Ollama is running and "
            f"the model {HERMES_MODEL} is pulled.",
        ) from e
    elapsed_ms = int((datetime.now(UTC) - start).total_seconds() * 1000)
    parsed: dict[str, Any] | None = None
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            parsed = decoded
    except json.JSONDecodeError:
        parsed = None
    return SkillResponse(
        skill=skill_name,
        model=HERMES_MODEL,
        parsed=parsed,
        raw=raw,
        elapsed_ms=elapsed_ms,
    )


# 1. select_method_for_task — used by NewExperiment form ───────────────────


class SelectMethodIn(BaseModel):
    task_description: str
    base_model: str | None = None
    dataset_name: str | None = None
    n_train_examples: int | None = None


@router.post("/select-method", response_model=SkillResponse)
def select_method(payload: SelectMethodIn) -> SkillResponse:
    """Ask Hermes which fine-tune method (lora/dora/full) fits the task."""
    if not payload.task_description.strip():
        raise HTTPException(400, "task_description is required")
    return _run_skill("select_method_for_task", payload.model_dump(exclude_none=True))


# 2. diagnose_mps_oom — used by RunDetail when a run failed ────────────────


def _tail_training_log(run_id: int, n: int = 80) -> str:
    """Return last ``n`` lines from runs/<id>/training.log if it exists."""
    candidates = [
        Path("/app/runs") / str(run_id) / "training.log",
        Path(__file__).resolve().parents[3] / "runs" / str(run_id) / "training.log",
    ]
    for p in candidates:
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[-n:])
            except OSError:
                continue
    return ""


@router.post("/diagnose-run/{run_id}", response_model=SkillResponse)
def diagnose_run(run_id: int, db: SessionDep) -> SkillResponse:
    """Diagnose a failed run via the diagnose_mps_oom skill.

    Sends the run config + tail of training.log + recorded error to Hermes
    and asks for a structured fix recommendation.
    """
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    payload = {
        "run_id": run.id,
        "status": run.status.value,
        "method": run.method.value,
        "base_model": run.base_model,
        "batch_size": run.batch_size,
        "num_layers": run.num_layers,
        "max_seq_length": run.max_seq_length,
        "iters": run.iters,
        "learning_rate": run.learning_rate,
        "error_message": run.error_message or "",
        "training_log_tail": _tail_training_log(run_id, 80),
    }
    return _run_skill("diagnose_mps_oom", payload)


# 3. analyze_canary_drift — used by ExperimentDetail when drift is high ────


def _session_iterations_summary(sid: int, db: Session) -> list[dict[str, Any]]:
    """Compact iteration history for the drift skill prompt."""
    rows = db.exec(
        select(Run).where(Run.session_id == sid).order_by(Run.iteration_number)
    ).all()
    return [
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
        for r in rows
        if r.iteration_number is not None
    ]


@router.post("/analyze-drift/{session_id}", response_model=SkillResponse)
def analyze_drift(session_id: int, db: SessionDep) -> SkillResponse:
    """Analyze canary drift for an autoresearch session.

    Sends the iteration history + drift threshold to Hermes and asks for a
    diagnosis + suggested mitigation (typically LR/num_layers reduction).
    """
    sess = db.get(TrainingSession, session_id)
    if not sess:
        raise HTTPException(404, "Session not found")
    iterations = _session_iterations_summary(session_id, db)
    # Compute current max drift to give the model an anchor.
    drifts = [
        abs((it["canary_loss"] or 0) - (it["val_loss"] or 0))
        for it in iterations
        if it.get("canary_loss") is not None and it.get("val_loss") is not None
    ]
    max_drift = max(drifts) if drifts else None
    payload = {
        "session_id": sess.id,
        "dataset": sess.dataset,
        "drift_threshold": sess.canary_drift_threshold,
        "max_observed_drift": max_drift,
        "iterations": iterations,
    }
    return _run_skill("analyze_canary_drift", payload)
