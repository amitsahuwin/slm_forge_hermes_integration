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


def _list_skill_paths() -> tuple[dict[str, Path], list[str]]:
    """Return (skill_name → resolved markdown path, dir_label_parts).

    Looks in two places and dedupes (later dirs win on conflict):
      1. Repo-level .hermes-skills/  — the source of truth in the repo
      2. SKILLS_DIR (defaults to ~/.hermes/skills) — what the bridge actually loads
    """
    found: dict[str, Path] = {}
    label_parts: list[str] = []

    repo_skills = _project_root() / ".hermes-skills"
    if repo_skills.exists():
        label_parts.append(str(repo_skills))
        for p in repo_skills.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found[p.stem] = p

    if SKILLS_DIR.exists() and repo_skills != SKILLS_DIR:
        label_parts.append(str(SKILLS_DIR))
        for p in SKILLS_DIR.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found[p.stem] = p

    return found, label_parts


def _list_skills() -> tuple[list[str], str]:
    """Backward-compat shim — preserves the (names, dir_label) signature
    used by ``hermes_status``."""
    found, label_parts = _list_skill_paths()
    label = " | ".join(label_parts) if label_parts else str(SKILLS_DIR)
    return sorted(found.keys()), label

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


# ─── Skills inspection — name + content for the Dashboard viewer ─────────────


class SkillSummary(BaseModel):
    name: str
    title: str           # first H1 from the markdown, or filename
    bytes: int
    path: str


class SkillContent(BaseModel):
    name: str
    title: str
    path: str
    content: str         # raw markdown


@router.get("/skills", response_model=list[SkillSummary])
def list_hermes_skills() -> list[SkillSummary]:
    """Enumerate every installed Hermes skill, with title + size.

    Used by the Dashboard's "13 installed" badge — clickable → opens a
    modal that fetches each skill's full content via ``/skills/{name}``.
    """
    paths, _ = _list_skill_paths()
    out: list[SkillSummary] = []
    for name in sorted(paths):
        p = paths[name]
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
        title = name
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s.lstrip("# ").strip() or name
                break
        out.append(
            SkillSummary(
                name=name,
                title=title,
                bytes=len(text.encode("utf-8")),
                path=str(p),
            )
        )
    return out


@router.get("/skills/{name}", response_model=SkillContent)
def get_hermes_skill(name: str) -> SkillContent:
    """Return one skill's full markdown body."""
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise HTTPException(400, "Invalid skill name.")
    paths, _ = _list_skill_paths()
    if name not in paths:
        raise HTTPException(404, f"Skill {name!r} not installed.")
    p = paths[name]
    try:
        content = p.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"Could not read skill: {e}") from e
    title = name
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("# "):
            title = s.lstrip("# ").strip() or name
            break
    return SkillContent(name=name, title=title, path=str(p), content=content)


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


def _run_skill(
    skill_name: str,
    user_payload: dict[str, Any],
    *,
    run_id: int | None = None,
    session_id: int | None = None,
) -> SkillResponse:
    """Common entrypoint for skill invocations.

    Loads the named skill markdown, calls Ollama with it as system prompt and
    ``user_payload`` (as JSON) as the user message. Parses the response as
    JSON when possible. Raises HTTPException(503) if Ollama is unreachable.

    Skill-Activity correlation: when an endpoint knows the originating run /
    session (e.g. ``/diagnose-run/{run_id}``), it should pass them so the
    resulting ``hermes_traces`` row carries the foreign keys.
    """
    from packages._log_context import binding

    loaded = load_skill(skill_name)
    if loaded is None:
        raise HTTPException(
            404,
            f"Skill {skill_name!r} not installed. Run `make hermes-install-skills` "
            "or verify the .hermes-skills/ directory is mounted.",
        )
    skill, skill_sha, skill_mtime = loaded
    user_msg = json.dumps(user_payload, default=str)
    start = datetime.now(UTC)
    try:
        with binding(run_id=run_id, session_id=session_id):
            raw = _call_ollama(
                skill,
                user_msg,
                expect_json=True,
                trace_source=f"skill:{skill_name}",
                skill_name=skill_name,
                skill_sha256=skill_sha,
                skill_mtime=skill_mtime,
            )
    except Exception as e:
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
    return _run_skill("diagnose_mps_oom", payload, run_id=run_id, session_id=run.session_id)


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
    return _run_skill("analyze_canary_drift", payload, session_id=session_id)


# ─── Phase N.2 / N.4 — eight new skill invocations ─────────────────────────


def _load_dataset_sample(dataset: str, n: int = 8) -> tuple[str, list[dict]]:
    """Load up to ``n`` training records from a dataset for skill input.

    Returns (description_from_readme, records). Empty list if dataset missing.
    """
    from pathlib import Path as _Path

    candidates = [
        _Path("/app/data/datasets") / dataset,
        _Path(__file__).resolve().parents[3] / "data" / "datasets" / dataset,
    ]
    ds_dir = next((c for c in candidates if c.exists()), None)
    if ds_dir is None:
        return "", []
    train = ds_dir / "train.jsonl"
    if not train.exists():
        return "", []
    rows: list[dict] = []
    with train.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    desc = ""
    readme = ds_dir / "README.md"
    if readme.exists():
        for raw in readme.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                desc = s
                break
    return desc, rows


# 4. data_quality_review — DatasetDetail ────────────────────────────────────


class DatasetReviewIn(BaseModel):
    dataset: str
    sample_size: int = 8


@router.post("/data-quality/{dataset}", response_model=SkillResponse)
def data_quality(dataset: str, sample_size: int = 8) -> SkillResponse:
    """Run the data_quality_review skill on a dataset sample."""
    desc, rows = _load_dataset_sample(dataset, n=max(1, min(sample_size, 50)))
    if not rows:
        raise HTTPException(404, f"Dataset {dataset!r} not found or empty.")
    return _run_skill(
        "data_quality_review",
        {"dataset": dataset, "description": desc, "sample": rows},
    )


# 5. propose_canary_set — DatasetDetail (when no canary) ────────────────────


@router.post("/propose-canary/{dataset}", response_model=SkillResponse)
def propose_canary(dataset: str) -> SkillResponse:
    """Generate a 5-record canary set proposal for a dataset."""
    desc, rows = _load_dataset_sample(dataset, n=10)
    if not rows:
        raise HTTPException(404, f"Dataset {dataset!r} not found or empty.")
    return _run_skill(
        "propose_canary_set",
        {"dataset": dataset, "description": desc, "sample": rows},
    )


class SaveCanaryIn(BaseModel):
    canary: list[dict[str, Any]]


@router.post("/propose-canary/{dataset}/save")
def save_canary(dataset: str, payload: SaveCanaryIn) -> dict[str, Any]:
    """Persist a proposed canary set to ``data/datasets/<dataset>/canary.jsonl``.

    Refuses to overwrite if a canary file already exists (caller can delete
    the file first if they really want to replace it). The Hermes review and
    canary chart will pick this up on next refresh.
    """
    if not payload.canary:
        raise HTTPException(400, "canary list is empty")
    from pathlib import Path as _Path

    candidates = [
        _Path("/app/data/datasets") / dataset,
        _Path(__file__).resolve().parents[3] / "data" / "datasets" / dataset,
    ]
    ds_dir = next((c for c in candidates if c.exists()), None)
    if ds_dir is None:
        raise HTTPException(404, f"Dataset {dataset!r} not found.")
    target = ds_dir / "canary.jsonl"
    if target.exists() and target.stat().st_size > 0:
        raise HTTPException(
            409,
            f"{target.name} already exists for {dataset!r}. Delete it manually first if you want to replace it.",
        )
    with target.open("w", encoding="utf-8") as f:
        for rec in payload.canary:
            f.write(json.dumps(rec) + "\n")
    return {"saved": str(target), "count": len(payload.canary)}


# 6. synthesize_style_prompt — SynthesizeModal ──────────────────────────────


@router.post("/synth-style/{dataset}", response_model=SkillResponse)
def synth_style(dataset: str) -> SkillResponse:
    """Build a dataset-specific style-guidance string for the synthesizer."""
    desc, rows = _load_dataset_sample(dataset, n=10)
    if not rows:
        raise HTTPException(404, f"Dataset {dataset!r} not found or empty.")
    return _run_skill(
        "synthesize_style_prompt",
        {"dataset": dataset, "description": desc, "sample": rows},
    )


# 7. explain_metric_anomaly — RunDetail auto-chip ──────────────────────────


@router.post("/explain-anomaly/{run_id}", response_model=SkillResponse)
def explain_anomaly(run_id: int, db: SessionDep) -> SkillResponse:
    """Inspect a run's metric series and explain any anomaly in plain English."""
    from apps.api.models.metric import Metric  # local to avoid import cycle

    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    metrics = db.exec(
        select(Metric).where(Metric.run_id == run_id).order_by(Metric.step, Metric.id)
    ).all()
    # Keep last 50 of each named series for prompt compactness.
    series: dict[str, list[dict[str, float]]] = {}
    for m in metrics:
        series.setdefault(m.name, []).append({"step": m.step, "value": m.value})
    for name, pts in series.items():
        if len(pts) > 50:
            series[name] = pts[-50:]
    payload = {
        "run_id": run_id,
        "config": {
            "method": run.method.value,
            "lr": run.learning_rate,
            "batch_size": run.batch_size,
            "num_layers": run.num_layers,
            "iters": run.iters,
        },
        "series": series,
        "final_train_loss": run.final_train_loss,
        "final_val_loss": run.final_val_loss,
        "canary_loss": run.canary_loss,
    }
    return _run_skill(
        "explain_metric_anomaly", payload, run_id=run_id, session_id=run.session_id
    )


# 8. recommend_export_quants — RunDetail export panel ──────────────────────


class RecommendQuantsIn(BaseModel):
    target_device: str = "iphone_pro"
    use_case: str = "chat"


@router.post("/recommend-quants/{run_id}", response_model=SkillResponse)
def recommend_quants(run_id: int, payload: RecommendQuantsIn, db: SessionDep) -> SkillResponse:
    """Recommend GGUF quant levels for a completed run + device."""
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return _run_skill(
        "recommend_export_quants",
        {
            "base_model": run.base_model,
            "method": run.method.value,
            "target_device": payload.target_device,
            "use_case": payload.use_case,
        },
        run_id=run_id,
        session_id=run.session_id,
    )


# 9. model_selection — NewExperiment second button ─────────────────────────


class ModelSelectionIn(BaseModel):
    task_description: str
    dataset_name: str | None = None
    n_train_examples: int | None = None
    target_device: str = "mac_desktop"


@router.post("/model-selection", response_model=SkillResponse)
def model_selection(payload: ModelSelectionIn) -> SkillResponse:
    """Recommend a base model for the task. Complements `/select-method`."""
    if not payload.task_description.strip():
        raise HTTPException(400, "task_description is required")
    return _run_skill("model_selection", payload.model_dump(exclude_none=True))


# 10. failure_post_mortem — RunDetail deeper diagnosis ─────────────────────


@router.post("/post-mortem/{run_id}", response_model=SkillResponse)
def post_mortem(run_id: int, db: SessionDep) -> SkillResponse:
    """Produce a markdown post-mortem for a failed (or otherwise) run.

    Broader than ``/diagnose-run`` (which is OOM-focused). Returns the full
    markdown body so the UI can render it; also writes a copy to
    ``runs/<id>/post_mortem.md`` for the run's artifact set.
    """
    from pathlib import Path as _Path

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
    resp = _run_skill(
        "failure_post_mortem", payload, run_id=run_id, session_id=run.session_id
    )

    # Best-effort: persist the markdown body alongside the run artifacts.
    try:
        candidates = [
            _Path("/app/runs") / str(run_id),
            _Path(__file__).resolve().parents[3] / "runs" / str(run_id),
        ]
        run_dir = next((c for c in candidates if c.exists()), None)
        if run_dir is None and candidates:
            run_dir = candidates[0]
            run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "post_mortem.md").write_text(resp.raw, encoding="utf-8")
    except OSError as e:
        log.warning("Could not persist post_mortem.md for run %s: %s", run_id, e)
    return resp


# 11. auto_label_unlabeled — NewDatasetV2 helper ───────────────────────────


class AutoLabelIn(BaseModel):
    text: str
    domain_hint: str | None = None


@router.post("/auto-label", response_model=SkillResponse)
def auto_label(payload: AutoLabelIn) -> SkillResponse:
    """Convert raw text into chat-style records via the auto_label_unlabeled skill.

    Returned ``raw`` is JSONL — caller parses each line as a separate record.
    """
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    # Cap at 6000 chars for prompt size; caller should chunk longer inputs.
    text = text[:6000]
    return _run_skill(
        "auto_label_unlabeled",
        {"text": text, "domain_hint": payload.domain_hint or ""},
    )
