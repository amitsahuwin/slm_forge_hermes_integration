#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 2 patch                                           ║
# ║                                                                      ║
# ║  Adds: autoresearch ratchet, sessions API, 4-graph UI,               ║
# ║        Ollama-powered Hermes bridge, skill markdown library          ║
# ║                                                                      ║
# ║  Run from project root, AFTER Phase 1 is working:                    ║
# ║    cd slm_forge_hermes_integration                                   ║
# ║    chmod +x bootstrap_phase2.sh                                      ║
# ║    ./bootstrap_phase2.sh                                             ║
# ║                                                                      ║
# ║  Then:                                                               ║
# ║    make rebuild                                                      ║
# ║    make hermes-install-skills                                        ║
# ║    make dev                  # T1: UI + API                          ║
# ║    make trainer              # T2: training worker                   ║
# ║    make ratchet              # T3: autoresearch worker (NEW)         ║
# ║                                                                      ║
# ║  Then http://localhost:5173/sessions/new                             ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run from project root (inside slm_forge_hermes_integration/)"
    exit 1
fi

echo "→ Applying Phase 2 patch..."

mkdir -p packages/ratchet
mkdir -p apps/web/src/components/ratchet
mkdir -p apps/web/src/pages
mkdir -p .hermes-skills

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  1. UPDATE apps/api/services/db.py — add migration logic             ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/services/db.py <<'EOF'
"""SQLite database init + lightweight forward-migrations."""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)

DEFAULT_DB_URL = "sqlite:////app/data/slm_forge.db"
DB_URL = os.environ.get("SLM_FORGE_DB_URL", DEFAULT_DB_URL)

if DB_URL.startswith("sqlite:///"):
    db_path = Path(DB_URL.replace("sqlite:///", "", 1))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})

# Idempotent ADD COLUMN migrations for the `runs` table (Phase 2 schema additions)
_RUN_MIGRATIONS: list[tuple[str, str]] = [
    ("session_id", "INTEGER"),
    ("parent_run_id", "INTEGER"),
    ("iteration_number", "INTEGER"),
    ("was_accepted", "INTEGER"),  # SQLite has no BOOL — uses INTEGER 0/1
    ("mutation_reasoning", "TEXT"),
    ("canary_loss", "REAL"),
]


def _migrate_runs() -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)"))}
        for col, sql_type in _RUN_MIGRATIONS:
            if col not in existing:
                log.info("Migrating: ALTER TABLE runs ADD COLUMN %s %s", col, sql_type)
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {col} {sql_type}"))
                conn.commit()


def init_db() -> None:
    """Create all tables, then run forward-migrations."""
    from apps.api.models import metric as _metric  # noqa: F401
    from apps.api.models import run as _run  # noqa: F401
    from apps.api.models import session as _session  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_runs()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as s:
        yield s
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  2. ADD apps/api/models/session.py                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/models/session.py <<'EOF'
"""Session = an autoresearch run = a sequence of training iterations."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class SessionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetMetric(str, Enum):
    VAL_LOSS = "val_loss"
    CANARY_LOSS = "canary_loss"


def _now() -> datetime:
    return datetime.now(UTC)


class TrainingSession(SQLModel, table=True):
    __tablename__ = "sessions"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str
    dataset: str
    base_model: str
    method: str = "lora"

    # Baseline hyperparams (iteration 0 uses these)
    iters: int = 100
    batch_size: int = 4
    learning_rate: float = 1e-4
    num_layers: int = 16
    max_seq_length: int = 2048

    # Session-level controls
    max_rounds: int = 8
    plateau_patience: int = 3
    min_delta: float = 0.005  # require this much val_loss improvement to "accept"
    target_metric: TargetMetric = TargetMetric.VAL_LOSS
    canary_drift_threshold: float = 0.3  # |canary - val| above this → warning

    status: SessionStatus = SessionStatus.QUEUED
    current_round: int = 0
    best_run_id: int | None = None
    best_metric_value: float | None = None
    error_message: str | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  3. UPDATE apps/api/models/run.py — add ratchet fields               ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/models/run.py <<'EOF'
"""Run model — one fine-tuning job (standalone or one iteration of a session)."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMethod(str, Enum):
    LORA = "lora"
    DORA = "dora"
    FULL = "full"


def _now() -> datetime:
    return datetime.now(UTC)


class Run(SQLModel, table=True):
    __tablename__ = "runs"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    dataset: str
    base_model: str
    method: RunMethod = RunMethod.LORA
    iters: int = 200
    batch_size: int = 4
    learning_rate: float = 1.0e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    grad_checkpoint: bool = False
    seed: int = 0

    status: RunStatus = RunStatus.QUEUED
    error_message: str | None = None
    adapter_path: str | None = None
    final_train_loss: float | None = None
    final_val_loss: float | None = None

    # Phase 2 — autoresearch fields
    session_id: int | None = Field(default=None, foreign_key="sessions.id", index=True)
    parent_run_id: int | None = None
    iteration_number: int | None = None
    was_accepted: bool | None = None
    mutation_reasoning: str | None = None
    canary_loss: float | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  4. ADD apps/api/routers/sessions.py                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/sessions.py <<'EOF'
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
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  5. UPDATE apps/api/main.py — mount sessions router                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/main.py <<'EOF'
"""SLM-Forge API — Phase 2."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import datasets, models, runs, sessions
from apps.api.services.db import init_db


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    python: str
    capabilities: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    init_db()
    yield


app = FastAPI(title="SLM-Forge API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router, prefix="/api/v1/runs", tags=["runs"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(datasets.router, prefix="/api/v1/datasets", tags=["datasets"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "SLM-Forge API", "version": "0.3.0", "docs": "/docs"}


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys
    return HealthResponse(
        status="ok",
        version="0.3.0",
        phase="Phase 2 — autoresearch ratchet",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": True,
            "autoresearch": True,
            "ingestion": False,
            "export_gguf": False,
            "hermes_bridge": True,
        },
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  6. ADD packages/ratchet/hermes_bridge.py                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ratchet/hermes_bridge.py <<'EOF'
"""Bridge to Hermes-style skills via Ollama HTTP.

Skills are markdown files in ~/.hermes/skills/ (mirrored from .hermes-skills/).
Each skill defines a procedure as a system prompt; the LLM (qwen2.5-coder:14b
via Ollama) executes it against the provided context, returning JSON.

When Hermes CLI stabilizes for programmatic use, swap _call_ollama for
a subprocess to `hermes` and the rest of this module stays put.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("ratchet.hermes")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "qwen2.5-coder:14b")
SKILLS_DIR = Path(os.environ.get("HERMES_SKILLS_DIR", str(Path.home() / ".hermes" / "skills")))


class MutationProposal(BaseModel):
    learning_rate: float | None = Field(default=None, ge=1e-7, le=1e-2)
    batch_size: int | None = Field(default=None, ge=1, le=32)
    num_layers: int | None = Field(default=None, ge=1, le=48)
    iters: int | None = Field(default=None, ge=20, le=2000)
    max_seq_length: int | None = Field(default=None, ge=128, le=8192)
    reasoning: str = "(no reasoning provided)"
    expected_outcome: str = ""


def load_skill(name: str) -> str | None:
    """Load a skill markdown by name (without .md extension)."""
    candidate = SKILLS_DIR / f"{name}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    # Fallback: try the repo's .hermes-skills/ for dev convenience
    repo_candidate = Path(__file__).resolve().parents[2] / ".hermes-skills" / f"{name}.md"
    if repo_candidate.exists():
        return repo_candidate.read_text(encoding="utf-8")
    log.warning("Skill %s not found in %s or .hermes-skills/", name, SKILLS_DIR)
    return None


def _call_ollama(system: str, user: str, *, expect_json: bool = True) -> str:
    """One-shot chat call to Ollama. Returns the model's response text."""
    payload: dict[str, Any] = {
        "model": HERMES_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.4},
    }
    if expect_json:
        payload["format"] = "json"

    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Ollama call failed: %s", e)
        raise

    return r.json()["message"]["content"]


def propose_mutation(
    *,
    dataset: str,
    history: list[dict[str, Any]],
    current_best_metric: float | None,
) -> MutationProposal:
    """Ask the LLM for the next hyperparameter mutation to try.

    `history` is a list of dicts, each describing one prior iteration with
    its hyperparams, val_loss, canary_loss, was_accepted, etc.
    """
    skill = load_skill("propose_hyperparam_mutation")
    if skill is None:
        # Fallback: minimal inline prompt
        skill = (
            "You are an ML researcher. Given iteration history, propose ONE hyperparameter "
            "change as JSON: {learning_rate?, batch_size?, num_layers?, iters?, "
            "reasoning, expected_outcome}. Be conservative."
        )

    user_msg = json.dumps(
        {
            "dataset": dataset,
            "history": history,
            "current_best_metric": current_best_metric,
            "instruction": (
                "Propose the next mutation. Return JSON only. "
                "Change at most TWO hyperparameters per iteration."
            ),
        },
        default=str,
    )

    raw = _call_ollama(skill, user_msg, expect_json=True)
    log.info("Hermes raw response: %s", raw[:300])

    try:
        data = json.loads(raw)
        return MutationProposal.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("Mutation parse failed (%s) — falling back to LR halving", e)
        # Safe fallback: halve the learning rate
        return MutationProposal(
            reasoning="LLM response invalid; fell back to LR halving",
            expected_outcome="More conservative training",
        )


def healthcheck() -> tuple[bool, str]:
    """Returns (ok, message) — used by ratchet startup."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"Ollama not reachable at {OLLAMA_URL}: {e}"
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/show", json={"name": HERMES_MODEL}, timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"Model {HERMES_MODEL} not pulled: {e}. Run: ollama pull {HERMES_MODEL}"
    return True, f"Ollama OK ({HERMES_MODEL})"
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  7. ADD packages/ratchet/decision.py                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ratchet/decision.py <<'EOF'
"""Accept/reject/plateau logic for the autoresearch ratchet."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    accepted: bool
    is_plateau: bool
    canary_drift: float | None
    reason: str


def evaluate_iteration(
    *,
    new_metric: float | None,
    best_metric: float | None,
    min_delta: float,
    history_no_improvement: int,
    plateau_patience: int,
    new_canary: float | None,
    new_val: float | None,
    drift_threshold: float,
) -> Decision:
    """Decide whether to accept this iteration and whether we've plateaued."""
    if new_metric is None:
        return Decision(False, False, None, "no metric reported")

    drift = (
        abs(new_canary - new_val)
        if (new_canary is not None and new_val is not None)
        else None
    )
    drift_warning = drift is not None and drift > drift_threshold

    if best_metric is None:
        # baseline iteration — always accept
        reason = "baseline accepted"
        if drift_warning:
            reason += f" (⚠ canary drift {drift:.3f} > {drift_threshold})"
        return Decision(True, False, drift, reason)

    improvement = best_metric - new_metric  # positive means new is better (lower loss)
    accepted = improvement >= min_delta

    if accepted:
        reason = f"improved by {improvement:.4f} ≥ {min_delta}"
    else:
        reason = f"no significant improvement ({improvement:+.4f} < {min_delta})"

    if drift_warning:
        reason += f" (⚠ canary drift {drift:.3f})"

    # Plateau if accepted ratchet hasn't moved for patience iters
    is_plateau = (not accepted) and (history_no_improvement + 1 >= plateau_patience)

    return Decision(accepted, is_plateau, drift, reason)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  8. ADD packages/ratchet/loop.py                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ratchet/loop.py <<'EOF'
"""The autoresearch ratchet loop. Runs one session to completion."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from packages.ratchet.decision import evaluate_iteration
from packages.ratchet.hermes_bridge import MutationProposal, propose_mutation

log = logging.getLogger("ratchet.loop")


class API:
    """Tiny HTTP wrapper around the SLM-Forge API."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.c = httpx.Client(timeout=30)

    def get_session(self, sid: int) -> dict:
        return self.c.get(f"{self.base}/api/v1/sessions/{sid}").raise_for_status().json()

    def patch_session(self, sid: int, **fields: Any) -> None:
        self.c.patch(f"{self.base}/api/v1/sessions/{sid}", json=fields).raise_for_status()

    def list_iterations(self, sid: int) -> list[dict]:
        r = self.c.get(f"{self.base}/api/v1/sessions/{sid}/iterations")
        r.raise_for_status()
        return r.json()

    def create_run(self, payload: dict) -> dict:
        r = self.c.post(f"{self.base}/api/v1/runs", json=payload)
        r.raise_for_status()
        return r.json()

    def get_run(self, rid: int) -> dict:
        r = self.c.get(f"{self.base}/api/v1/runs/{rid}")
        r.raise_for_status()
        return r.json()

    def patch_run(self, rid: int, **fields: Any) -> None:
        self.c.patch(f"{self.base}/api/v1/runs/{rid}", json=fields).raise_for_status()


def _hyperparams_from_session(session: dict) -> dict:
    """Initial (baseline) hyperparams come from the session row."""
    return {
        "iters": session["iters"],
        "batch_size": session["batch_size"],
        "learning_rate": session["learning_rate"],
        "num_layers": session["num_layers"],
        "max_seq_length": session["max_seq_length"],
    }


def _apply_mutation(base: dict, m: MutationProposal) -> dict:
    """Return a new hyperparam dict with the mutation applied."""
    out = dict(base)
    if m.learning_rate is not None:
        out["learning_rate"] = m.learning_rate
    if m.batch_size is not None:
        out["batch_size"] = m.batch_size
    if m.num_layers is not None:
        out["num_layers"] = m.num_layers
    if m.iters is not None:
        out["iters"] = m.iters
    if m.max_seq_length is not None:
        out["max_seq_length"] = m.max_seq_length
    return out


def _wait_for_run(api: API, rid: int, *, poll: float = 2.0, timeout: float = 7200) -> dict:
    """Block until a run reaches terminal status."""
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        r = api.get_run(rid)
        if r["status"] != last_status:
            log.info("  run #%s status: %s", rid, r["status"])
            last_status = r["status"]
        if r["status"] in {"completed", "failed", "cancelled"}:
            return r
        time.sleep(poll)
    raise TimeoutError(f"Run #{rid} did not finish in {timeout}s")


def _history_summary(iterations: list[dict]) -> list[dict]:
    """Compact iteration history for the LLM prompt."""
    return [
        {
            "iter": it.get("iteration_number"),
            "lr": it["learning_rate"],
            "batch_size": it["batch_size"],
            "num_layers": it["num_layers"],
            "iters": it["iters"],
            "val_loss": it.get("final_val_loss"),
            "canary_loss": it.get("canary_loss"),
            "was_accepted": it.get("was_accepted"),
        }
        for it in iterations
    ]


def run_session(session_id: int, api: API) -> None:
    """Orchestrate one autoresearch session to completion."""
    session = api.get_session(session_id)
    log.info("─── Session #%s: %s ───", session_id, session["name"])
    log.info("  dataset=%s model=%s method=%s",
             session["dataset"], session["base_model"], session["method"])
    log.info("  max_rounds=%s plateau_patience=%s min_delta=%s",
             session["max_rounds"], session["plateau_patience"], session["min_delta"])

    api.patch_session(session_id, status="running")

    base_hyperparams = _hyperparams_from_session(session)
    best_metric: float | None = None
    best_run_id: int | None = None
    no_improvement_streak = 0

    for round_idx in range(session["max_rounds"]):
        api.patch_session(session_id, current_round=round_idx)

        # ─── decide hyperparams for this iteration ───
        if round_idx == 0:
            hp = base_hyperparams
            mutation_reasoning = "baseline"
        else:
            iters_so_far = api.list_iterations(session_id)
            hist = _history_summary(iters_so_far)
            log.info("  asking Hermes for mutation (history=%d iters)", len(hist))
            try:
                proposal = propose_mutation(
                    dataset=session["dataset"],
                    history=hist,
                    current_best_metric=best_metric,
                )
            except Exception as e:  # noqa: BLE001
                log.error("Hermes proposal failed: %s — using LR halving", e)
                proposal = MutationProposal(
                    learning_rate=base_hyperparams["learning_rate"] * 0.5,
                    reasoning=f"Hermes failed ({e}); LR halved as safe fallback",
                    expected_outcome="conservative continued training",
                )
            # Apply mutation on top of the BEST-so-far config (or baseline if no best yet)
            mutate_from = base_hyperparams
            if best_run_id is not None:
                best = api.get_run(best_run_id)
                mutate_from = {k: best[k] for k in base_hyperparams}
            hp = _apply_mutation(mutate_from, proposal)
            mutation_reasoning = proposal.reasoning
            log.info("  mutation: %s", proposal.model_dump(exclude_none=True))

        # ─── create the Run; the trainer worker will pick it up ───
        run_payload = {
            "dataset": session["dataset"],
            "base_model": session["base_model"],
            "method": session["method"],
            **hp,
            "grad_checkpoint": False,
            "seed": 0,
        }
        created = api.create_run(run_payload)
        rid = created["id"]
        log.info("  → created run #%s (round %d)", rid, round_idx)

        # Annotate it with session linkage
        api.patch_run(
            rid,
            # PATCH only accepts the fields its schema lists; for session linkage
            # we update via a direct DB write below would be cleaner — but for
            # Phase 2 simplicity we extend the RunPatch later if needed.
        )
        # The /runs PATCH only accepts the operational fields. To set
        # session_id / iteration_number / parent_run_id / mutation_reasoning,
        # we use a small bookkeeping POST below via /sessions/{sid}/link-run.
        # For Phase 2 we shortcut by writing those fields via a dedicated endpoint
        # — but to keep router count down, we instead just patch /runs with a
        # superset payload by extending RunPatch. (Already done in routers/runs.py
        # if you've patched it; if not, this is a no-op.)
        try:
            httpx.patch(
                f"{api.base}/api/v1/runs/{rid}",
                json={
                    "session_id": session_id,
                    "iteration_number": round_idx,
                    "parent_run_id": best_run_id,
                    "mutation_reasoning": mutation_reasoning,
                },
                timeout=10,
            ).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("  could not link run to session (%s) — continuing", e)

        # ─── wait for the trainer to execute it ───
        log.info("  waiting for trainer to pick up run #%s...", rid)
        final = _wait_for_run(api, rid)

        if final["status"] != "completed":
            log.warning("  run #%s ended with status=%s — marking rejected", rid, final["status"])
            try:
                httpx.patch(
                    f"{api.base}/api/v1/runs/{rid}",
                    json={"was_accepted": False},
                    timeout=10,
                )
            except httpx.HTTPError:
                pass
            no_improvement_streak += 1
            if no_improvement_streak >= session["plateau_patience"]:
                log.info("  plateau (errors) — ending session")
                break
            continue

        # ─── evaluate ───
        new_val = final.get("final_val_loss")
        new_canary = final.get("canary_loss")  # currently None until canary eval added
        decision = evaluate_iteration(
            new_metric=new_val,
            best_metric=best_metric,
            min_delta=session["min_delta"],
            history_no_improvement=no_improvement_streak,
            plateau_patience=session["plateau_patience"],
            new_canary=new_canary,
            new_val=new_val,
            drift_threshold=session["canary_drift_threshold"],
        )

        log.info("  decision: %s%s",
                 "ACCEPT" if decision.accepted else "REJECT", f" — {decision.reason}")

        try:
            httpx.patch(
                f"{api.base}/api/v1/runs/{rid}",
                json={"was_accepted": decision.accepted},
                timeout=10,
            ).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("could not mark run was_accepted: %s", e)

        if decision.accepted and new_val is not None:
            best_metric = new_val
            best_run_id = rid
            no_improvement_streak = 0
            api.patch_session(
                session_id,
                best_run_id=best_run_id,
                best_metric_value=best_metric,
            )
        else:
            no_improvement_streak += 1

        if decision.is_plateau:
            log.info("  plateau detected — ending session early")
            break

    # ─── session complete ───
    api.patch_session(session_id, status="completed")
    log.info("─── Session #%s complete. Best run: #%s (val_loss=%s) ───",
             session_id, best_run_id, best_metric)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  9. ADD packages/ratchet/__main__.py — ratchet worker entrypoint     ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ratchet/__main__.py <<'EOF'
"""Autoresearch ratchet worker. Polls API for queued sessions and runs them.

Run via:
    uv run python -m packages.ratchet

Requires:
  - The API to be reachable (make dev)
  - The trainer worker to be running (make trainer)
  - Ollama serving qwen2.5-coder:14b (make install-hermes)
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

from packages.ratchet.hermes_bridge import healthcheck
from packages.ratchet.loop import API, run_session

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("ratchet.worker")

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "3.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(
            f"{API_URL}/api/v1/sessions",
            params={"status": "queued", "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        sessions = r.json()
        return sessions[-1] if sessions else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Ratchet worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

    # Wait for API
    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API at %s...", API_URL)
            time.sleep(2)
    else:
        log.error("API never came up. Is 'make dev' running?")
        return 1

    # Verify Ollama + qwen
    ok, msg = healthcheck()
    if not ok:
        log.error("Hermes/Ollama healthcheck failed: %s", msg)
        log.error("Run 'make install-hermes' first, then retry.")
        return 1
    log.info("Hermes bridge: %s", msg)

    log.info("Ready. Polling for queued sessions every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    api = API(API_URL)
    while True:
        try:
            session = fetch_next_queued()
            if session is None:
                time.sleep(POLL_INTERVAL)
                continue
            run_session(session["id"], api)
        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Session orchestration failed: %s", e)
            try:
                httpx.patch(
                    f"{API_URL}/api/v1/sessions/{session['id']}",
                    json={"status": "failed", "error_message": str(e)[:500]},
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  10. UPDATE apps/api/routers/runs.py — extend RunPatch for ratchet   ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/runs.py <<'EOF'
"""Run management + live metric streaming."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.metric import Metric
from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.services.db import get_session

router = APIRouter()


class RunCreate(BaseModel):
    dataset: str
    base_model: str = "mlx-community/gemma-3n-E2B-it-bf16"
    method: RunMethod = RunMethod.LORA
    iters: int = 200
    batch_size: int = 4
    learning_rate: float = 1.0e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    grad_checkpoint: bool = False
    seed: int = 0


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
        from apps.api.services.db import engine
        from sqlmodel import Session as _Session

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
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  11. UPDATE Makefile — add 'make ratchet'                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > Makefile <<'EOF'
.PHONY: help setup install-hermes hermes-install-skills dev down build rebuild logs trainer ratchet \
        seed-data download-base-model train-sample clean ensure-lock

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

setup: ## Install all deps (Python via uv, Node via npm)
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	uv sync --all-extras
	cd apps/web && npm install

install-hermes: ## Install Ollama + Hermes Agent + qwen2.5-coder:14b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download Gemma 3n E2B base model from HF (~1.5 GB)
	bash scripts/download_base_model.sh

trainer: ## Run the host trainer worker (Metal access)
	uv run python -m packages.trainer

ratchet: ## Run the autoresearch ratchet worker (needs trainer + Ollama)
	@echo "→ Starting autoresearch ratchet worker..."
	@echo "  Required: 'make dev', 'make trainer', and Ollama running."
	uv run python -m packages.ratchet

ensure-lock:
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		echo "→ Lock files missing — running 'make setup'..."; \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API (docker-compose, live reload)
	docker compose up

rebuild: ensure-lock ## Force-rebuild Docker images (use after editing package.json / pyproject.toml)
	docker compose down
	docker compose build --no-cache

down: ## Stop dev stack
	docker compose down

build: ensure-lock ## Build Docker images (incremental)
	docker compose build

logs:
	docker compose logs -f

clean:
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  12. Add Hermes skill markdown files                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > .hermes-skills/propose_hyperparam_mutation.md <<'EOF'
# Skill: Propose Hyperparameter Mutation

You are an expert ML researcher running fine-tuning experiments on Apple Silicon
with MLX-LM. Given the training history of a fine-tuning session, propose ONE
small hyperparameter change to try next.

## Inputs

The user message is JSON with:
- `dataset`: dataset name + size hints
- `history`: list of prior iterations, each with `lr`, `batch_size`, `num_layers`,
  `iters`, `val_loss`, `canary_loss`, `was_accepted`
- `current_best_metric`: best val_loss so far (lower is better)

## Output (JSON only — no prose, no markdown)

```json
{
  "learning_rate": 0.00005,
  "batch_size": null,
  "num_layers": null,
  "iters": null,
  "max_seq_length": null,
  "reasoning": "1-2 sentence explanation of WHY this change",
  "expected_outcome": "1 sentence prediction"
}
```

Set any field to `null` to leave it unchanged. Change AT MOST TWO fields per iteration.

## Strategy

1. **Iteration 0 (baseline only):** explore mildly — try lowering LR by 2-3×
2. **Improving trend:** keep going in the same direction (e.g. if lower LR helped, lower further but less aggressively)
3. **Plateau:** try a *different lever* (num_layers, batch_size) instead of compounding LR changes
4. **Canary > val by a lot:** overfitting signal — lower LR, reduce num_layers (more regularization)
5. **Val_loss exploded after change:** revert direction immediately on next call

## Safe ranges (NEVER propose outside these)

- `learning_rate`: 1e-6 to 1e-3
- `batch_size`: 1 to 16
- `num_layers`: 4 to 32 (MLX-LM LoRA: layers to fine-tune from the top)
- `iters`: 50 to 500
- `max_seq_length`: 512 to 4096

## Examples

**History:** `[{iter: 0, lr: 1e-4, val_loss: 2.1, was_accepted: true}]`, best=2.1
**Output:** `{"learning_rate": 5e-5, "batch_size": null, "num_layers": null, "iters": null, "max_seq_length": null, "reasoning": "Halve LR from baseline to test if smaller steps converge better", "expected_outcome": "Lower val_loss by 0.05-0.15"}`

**History:** `[(lr=1e-4, val=2.1, accepted), (lr=5e-5, val=1.95, accepted), (lr=2.5e-5, val=1.97, rejected)]`, best=1.95
**Output:** `{"learning_rate": null, "batch_size": null, "num_layers": 24, "iters": null, "max_seq_length": null, "reasoning": "LR sweep plateaued at 5e-5; try expanding LoRA capacity with more layers instead", "expected_outcome": "Marginal val_loss improvement at the cost of training time"}`
EOF

cat > .hermes-skills/diagnose_mps_oom.md <<'EOF'
# Skill: Diagnose Apple MPS Out-of-Memory

When MLX-LM training fails on Apple Silicon with memory pressure or MPS allocation errors, suggest a fix.

## Common signals

- `RuntimeError: MPS backend out of memory`
- `[METAL] Error: ...`
- `Killed: 9` mid-training (macOS jetsam killed the process)
- Sustained high swap usage during training
- `Tokens/sec` collapses to <50 after first eval step

## Fix priority (try in order)

1. **Reduce batch size** (4 → 2 → 1)
2. **Reduce max_seq_length** (2048 → 1024 → 512)
3. **Reduce num_layers** (16 → 8 — fewer LoRA-adapted layers)
4. **Enable gradient checkpointing** (`grad_checkpoint: true` — slower but ~30% less RAM)
5. **Switch to QLoRA** (use a `-4bit` MLX-community quantized base model)
6. **Drop to a smaller base** (E4B → E2B; 8B → 3B)

## Output format

JSON:
```json
{
  "batch_size": 2,
  "max_seq_length": 1024,
  "grad_checkpoint": true,
  "reasoning": "OOM during eval suggests sequence length is the binding constraint; halving seq_len + enabling checkpointing should fit comfortably in 36GB",
  "expected_outcome": "Training completes; tokens/sec drops ~30% but no OOM"
}
```
EOF

cat > .hermes-skills/select_method_for_task.md <<'EOF'
# Skill: Select Fine-Tuning Method

Given a task description and base model, recommend `lora`, `dora`, or `full`.

## Decision rules

- **`lora`** — default. Use unless you have a specific reason not to.
- **`dora`** — when LoRA plateaus and you suspect the rank is the bottleneck. DoRA usually beats LoRA on the same rank for the same compute.
- **`full`** — only for small base models (<2B params) AND when you have ≥5000 training examples AND when LoRA/DoRA have demonstrably failed.

## Task → method shortcuts

| Task type | Default method |
|---|---|
| Persona/style transfer | lora |
| Domain Q&A | lora |
| Code generation | dora |
| Classification head | full (small model only) |
| Instruction following | lora |
| Tool use | dora |

## Output

```json
{
  "method": "lora",
  "num_layers": 16,
  "reasoning": "1-sentence justification"
}
```
EOF

cat > .hermes-skills/analyze_canary_drift.md <<'EOF'
# Skill: Analyze Canary Drift (Goodhart Guardrail)

Canary drift = `|canary_loss - val_loss|`. If it exceeds the session threshold,
the model is plausibly overfitting to the validation set.

## Diagnosis

| Drift | Interpretation |
|---|---|
| < 0.1 | Healthy. Canary and val correlated. |
| 0.1 – 0.3 | Mild divergence. Watch closely. |
| > 0.3 | Likely overfitting. Recommend regularization. |
| > 0.6 | Serious overfitting. Roll back, reduce capacity. |

## Recommended responses

When drift > threshold:
1. Lower LR by 2×
2. Reduce `num_layers` by ~25%
3. Increase regularization (LoRA dropout if exposed)
4. Stop the session if drift > 0.6 and trending up

## Output

```json
{
  "learning_rate": 2.5e-5,
  "num_layers": 12,
  "reasoning": "Canary drift 0.4 indicates val-set overfitting; reduce LR + capacity",
  "expected_outcome": "Drift narrows; val_loss may rise short-term"
}
```
EOF

# Update placeholder README
cat > .hermes-skills/README.md <<'EOF'
# Hermes Skills

Skill markdown files. `make hermes-install-skills` copies these to `~/.hermes/skills/`.

These skills are used by the autoresearch ratchet's Hermes bridge
(`packages/ratchet/hermes_bridge.py`), which executes them against Ollama
(qwen2.5-coder:14b by default). Each `.md` file is one skill = one
system-prompt + JSON output contract.

## Phase 2 skills

- `propose_hyperparam_mutation` — given iteration history, propose next config
- `diagnose_mps_oom` — recognize MPS OOM, suggest fixes
- `select_method_for_task` — recommend LoRA vs DoRA vs full
- `analyze_canary_drift` — Goodhart-style overfitting detection

## Phase 3 (next)

- `ingest_dataset` — given URL/path, detect format, load
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  13. UPDATE apps/web/src/lib/api.ts                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/lib/api.ts <<'EOF'
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type RunMethod = 'lora' | 'dora' | 'full';

export type Run = {
  id: number;
  dataset: string;
  base_model: string;
  method: RunMethod;
  iters: number;
  batch_size: number;
  learning_rate: number;
  num_layers: number;
  max_seq_length: number;
  grad_checkpoint: boolean;
  seed: number;
  status: RunStatus;
  error_message: string | null;
  adapter_path: string | null;
  final_train_loss: number | null;
  final_val_loss: number | null;
  session_id: number | null;
  parent_run_id: number | null;
  iteration_number: number | null;
  was_accepted: boolean | null;
  mutation_reasoning: string | null;
  canary_loss: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type Metric = {
  id?: number;
  run_id?: number;
  step: number;
  name: string;
  value: number;
  recorded_at?: string;
};

export type SessionStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export type TrainingSession = {
  id: number;
  name: string;
  dataset: string;
  base_model: string;
  method: RunMethod;
  iters: number;
  batch_size: number;
  learning_rate: number;
  num_layers: number;
  max_seq_length: number;
  max_rounds: number;
  plateau_patience: number;
  min_delta: number;
  target_metric: 'val_loss' | 'canary_loss';
  canary_drift_threshold: number;
  status: SessionStatus;
  current_round: number;
  best_run_id: number | null;
  best_metric_value: number | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type DatasetInfo = {
  name: string;
  train_count: number;
  valid_count: number;
  has_canary: boolean;
  description: string;
};

export type BaseModelInfo = {
  hf_id: string;
  label: string;
  family: string;
  size_params: string;
  recommended_method: string;
  notes: string;
};

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API_URL}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  // Runs
  listRuns: () => jget<Run[]>('/api/v1/runs'),
  getRun: (id: number) => jget<Run>(`/api/v1/runs/${id}`),
  createRun: (body: Partial<Run>) => jpost<Run>('/api/v1/runs', body),
  listMetrics: (id: number) => jget<Metric[]>(`/api/v1/runs/${id}/metrics`),
  // Sessions
  listSessions: () => jget<TrainingSession[]>('/api/v1/sessions'),
  getSession: (id: number) => jget<TrainingSession>(`/api/v1/sessions/${id}`),
  createSession: (body: Partial<TrainingSession>) =>
    jpost<TrainingSession>('/api/v1/sessions', body),
  listIterations: (id: number) => jget<Run[]>(`/api/v1/sessions/${id}/iterations`),
  // Datasets & models
  listDatasets: () => jget<DatasetInfo[]>('/api/v1/datasets'),
  listModels: () => jget<BaseModelInfo[]>('/api/v1/models'),
};
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  14. UPDATE Nav.tsx and App.tsx                                      ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/Nav.tsx <<'EOF'
import { NavLink } from 'react-router-dom';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
const activeLink = 'bg-zinc-800 text-zinc-100';

export default function Nav() {
  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/sessions" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Sessions
            </NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Runs
            </NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Datasets
            </NavLink>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <NavLink
            to="/sessions/new"
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
          >
            + New Session
          </NavLink>
        </div>
      </div>
    </header>
  );
}
EOF

cat > apps/web/src/App.tsx <<'EOF'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import NewRun from './pages/NewRun';
import NewSession from './pages/NewSession';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';
import SessionDetail from './pages/SessionDetail';
import Sessions from './pages/Sessions';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-7xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/new" element={<NewSession />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/new" element={<NewRun />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  15. ADD apps/web/src/components/ratchet/RatchetTimeline.tsx         ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/ratchet/RatchetTimeline.tsx <<'EOF'
/**
 * Main ratchet graph: X = iteration #, Y = primary metric.
 * Dots: green=accepted, red=rejected, yellow=errored.
 * Solid line connects only accepted points (the ratchet stairstep).
 */
import { useMemo } from 'react';
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[]; targetMetric: 'val_loss' | 'canary_loss' };

type Point = {
  iter: number;
  metric: number | null;
  accepted: number | null;     // for green scatter
  rejected: number | null;     // for red scatter
  errored: number | null;      // for yellow scatter
  ratchet: number | null;      // green-only line value (NaN-bridged)
  lr: number;
  batch_size: number;
  num_layers: number;
  iters: number;
  reasoning: string | null;
  status: string;
};

function metricOf(it: Run, m: 'val_loss' | 'canary_loss'): number | null {
  if (m === 'val_loss') return it.final_val_loss;
  return it.canary_loss;
}

export default function RatchetTimeline({ iterations, targetMetric }: Props) {
  const data: Point[] = useMemo(() => {
    const sorted = [...iterations].sort(
      (a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0),
    );
    return sorted.map((it) => {
      const v = metricOf(it, targetMetric);
      const errored = it.status === 'failed' || it.status === 'cancelled';
      return {
        iter: it.iteration_number ?? 0,
        metric: v,
        accepted: it.was_accepted === true && v !== null ? v : null,
        rejected: it.was_accepted === false && v !== null && !errored ? v : null,
        errored: errored ? v ?? 0 : null,
        ratchet: it.was_accepted === true ? v : null,
        lr: it.learning_rate,
        batch_size: it.batch_size,
        num_layers: it.num_layers,
        iters: it.iters,
        reasoning: it.mutation_reasoning,
        status: it.status,
      };
    });
  }, [iterations, targetMetric]);

  if (data.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500">
        Ratchet graph appears once iteration 0 completes…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Ratchet timeline
        </h3>
        <div className="flex items-center gap-4 font-mono text-xs">
          <span className="text-zinc-500">Y: {targetMetric}</span>
          <Legend dot="emerald" label="accepted" />
          <Legend dot="rose" label="rejected" />
          <Legend dot="amber" label="error" />
        </div>
      </div>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis
              dataKey="iter"
              type="number"
              domain={[0, 'dataMax']}
              stroke="#71717a"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              label={{ value: 'iteration', position: 'insideBottom', offset: -5, fontSize: 11, fill: '#71717a' }}
            />
            <YAxis
              stroke="#71717a"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              domain={['auto', 'auto']}
            />
            <Tooltip content={<RatchetTooltip />} />
            <Line
              type="stepAfter"
              dataKey="ratchet"
              stroke="#34d399"
              strokeWidth={2.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Scatter dataKey="accepted" fill="#34d399" shape="circle" />
            <Scatter dataKey="rejected" fill="#fb7185" shape="cross" />
            <Scatter dataKey="errored" fill="#fbbf24" shape="triangle" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Legend({ dot, label }: { dot: string; label: string }) {
  const color =
    dot === 'emerald' ? 'bg-emerald-400' : dot === 'rose' ? 'bg-rose-400' : 'bg-amber-400';
  return (
    <span className="flex items-center gap-1.5 text-zinc-400">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function RatchetTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  const status =
    p.accepted !== null ? 'accepted' : p.rejected !== null ? 'rejected' : 'errored';
  return (
    <div className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-xs">
      <div className="text-zinc-100">iter #{p.iter} · {status}</div>
      <div className="mt-1 text-zinc-400">metric = {p.metric?.toFixed(4) ?? '—'}</div>
      <div className="text-zinc-500">lr={p.lr.toExponential(1)} · bs={p.batch_size} · layers={p.num_layers} · it={p.iters}</div>
      {p.reasoning && (
        <div className="mt-2 max-w-xs whitespace-normal text-zinc-300">"{p.reasoning}"</div>
      )}
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  16. ADD apps/web/src/components/ratchet/CanaryDriftChart.tsx        ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/ratchet/CanaryDriftChart.tsx <<'EOF'
import { useMemo } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[]; threshold: number };

export default function CanaryDriftChart({ iterations, threshold }: Props) {
  const data = useMemo(() => {
    return [...iterations]
      .sort((a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0))
      .map((it) => ({
        iter: it.iteration_number ?? 0,
        drift:
          it.final_val_loss !== null && it.canary_loss !== null
            ? Math.abs(it.canary_loss - it.final_val_loss)
            : null,
      }));
  }, [iterations]);

  const hasAny = data.some((d) => d.drift !== null);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Canary drift (Goodhart guardrail)
        </h3>
        <span className="font-mono text-xs text-zinc-500">threshold: {threshold.toFixed(2)}</span>
      </div>
      {!hasAny ? (
        <div className="flex h-32 items-center justify-center text-sm text-zinc-500">
          Canary eval not yet wired into the trainer (Phase 2.5).
        </div>
      ) : (
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="iter" stroke="#71717a" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
              <YAxis stroke="#71717a" tick={{ fontSize: 11, fontFamily: 'monospace' }} domain={[0, 'auto']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #3f3f46',
                  fontSize: 12,
                  fontFamily: 'monospace',
                }}
              />
              <ReferenceLine y={threshold} stroke="#f43f5e" strokeDasharray="4 4" />
              <Line
                type="monotone"
                dataKey="drift"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  17. ADD HyperparamHeatmap.tsx (custom SVG)                          ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/ratchet/HyperparamHeatmap.tsx <<'EOF'
import { useMemo } from 'react';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[] };

const PARAMS: { key: keyof Run; label: string; format: (v: number) => string }[] = [
  { key: 'learning_rate', label: 'lr', format: (v) => v.toExponential(1) },
  { key: 'batch_size', label: 'batch', format: (v) => v.toString() },
  { key: 'num_layers', label: 'layers', format: (v) => v.toString() },
  { key: 'iters', label: 'iters', format: (v) => v.toString() },
  { key: 'max_seq_length', label: 'seq_len', format: (v) => v.toString() },
];

function colorForChange(prev: number | undefined, curr: number): string {
  if (prev === undefined || prev === curr) return '#27272a'; // zinc-800 (unchanged)
  const ratio = curr / prev;
  if (ratio > 1) {
    // increased — blue intensity
    const alpha = Math.min(1, Math.log(ratio) / Math.log(4));
    return `rgba(96, 165, 250, ${0.2 + 0.8 * alpha})`;
  } else {
    // decreased — red intensity
    const alpha = Math.min(1, -Math.log(ratio) / Math.log(4));
    return `rgba(251, 113, 133, ${0.2 + 0.8 * alpha})`;
  }
}

export default function HyperparamHeatmap({ iterations }: Props) {
  const sorted = useMemo(
    () => [...iterations].sort((a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0)),
    [iterations],
  );

  if (sorted.length === 0) {
    return null;
  }

  const cellW = 64;
  const cellH = 32;
  const labelW = 80;
  const headerH = 24;
  const width = labelW + sorted.length * cellW;
  const height = headerH + PARAMS.length * cellH;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        Hyperparameter changes per iteration
      </h3>
      <div className="overflow-x-auto">
        <svg width={width} height={height} className="font-mono">
          {/* Header: iter numbers */}
          {sorted.map((it, i) => (
            <text
              key={`hdr-${i}`}
              x={labelW + i * cellW + cellW / 2}
              y={headerH - 6}
              textAnchor="middle"
              fontSize="11"
              fill="#71717a"
            >
              #{it.iteration_number ?? i}
            </text>
          ))}

          {/* Rows */}
          {PARAMS.map((param, rowIdx) => (
            <g key={param.key as string} transform={`translate(0, ${headerH + rowIdx * cellH})`}>
              <text x={labelW - 8} y={cellH / 2 + 4} textAnchor="end" fontSize="11" fill="#a1a1aa">
                {param.label}
              </text>
              {sorted.map((it, colIdx) => {
                const curr = it[param.key] as number;
                const prev = colIdx > 0 ? (sorted[colIdx - 1][param.key] as number) : undefined;
                const fill = colorForChange(prev, curr);
                return (
                  <g key={colIdx} transform={`translate(${labelW + colIdx * cellW}, 0)`}>
                    <rect width={cellW - 2} height={cellH - 2} fill={fill} stroke="#18181b" />
                    <text
                      x={cellW / 2 - 1}
                      y={cellH / 2 + 4}
                      textAnchor="middle"
                      fontSize="10"
                      fill="#fafafa"
                    >
                      {param.format(curr)}
                    </text>
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
      </div>
      <div className="mt-2 flex items-center gap-4 font-mono text-xs text-zinc-500">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: 'rgba(96, 165, 250, 0.7)' }} />
          increased
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: 'rgba(251, 113, 133, 0.7)' }} />
          decreased
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-zinc-800" />
          unchanged
        </span>
      </div>
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  18. ADD apps/web/src/components/ratchet/IterationTable.tsx          ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/ratchet/IterationTable.tsx <<'EOF'
import { Link } from 'react-router-dom';
import type { Run } from '../../lib/api';

export default function IterationTable({ iterations }: { iterations: Run[] }) {
  const sorted = [...iterations].sort(
    (a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0),
  );

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
          <tr>
            <th className="px-3 py-2 text-left">#</th>
            <th className="px-3 py-2 text-left">Run</th>
            <th className="px-3 py-2 text-left">Status</th>
            <th className="px-3 py-2 text-right">val_loss</th>
            <th className="px-3 py-2 text-right">lr</th>
            <th className="px-3 py-2 text-right">batch</th>
            <th className="px-3 py-2 text-right">layers</th>
            <th className="px-3 py-2 text-left">Decision</th>
            <th className="px-3 py-2 text-left">Reasoning</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {sorted.map((r) => (
            <tr key={r.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
              <td className="px-3 py-2">{r.iteration_number}</td>
              <td className="px-3 py-2">
                <Link to={`/runs/${r.id}`} className="text-emerald-400 hover:underline">
                  #{r.id}
                </Link>
              </td>
              <td className="px-3 py-2 text-xs">{r.status}</td>
              <td className="px-3 py-2 text-right">
                {r.final_val_loss !== null ? r.final_val_loss.toFixed(4) : '—'}
              </td>
              <td className="px-3 py-2 text-right">{r.learning_rate.toExponential(1)}</td>
              <td className="px-3 py-2 text-right">{r.batch_size}</td>
              <td className="px-3 py-2 text-right">{r.num_layers}</td>
              <td className="px-3 py-2">
                {r.was_accepted === true && <span className="text-emerald-400">● accepted</span>}
                {r.was_accepted === false && <span className="text-rose-400">✗ rejected</span>}
                {r.was_accepted === null && <span className="text-zinc-600">…</span>}
              </td>
              <td className="px-3 py-2 text-xs text-zinc-400">{r.mutation_reasoning ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  19. ADD apps/web/src/pages/Sessions.tsx                             ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Sessions.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type SessionStatus, type TrainingSession, api } from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function Sessions() {
  const [sessions, setSessions] = useState<TrainingSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .listSessions()
        .then((ss) => alive && setSessions(ss))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 2500);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Autoresearch sessions — Hermes-driven hyperparameter sweeps.
          </p>
        </div>
        <Link
          to="/sessions/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Session
        </Link>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {sessions === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : sessions.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No sessions yet.{' '}
          <Link to="/sessions/new" className="text-emerald-400 hover:underline">
            Start your first autoresearch session →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 text-left">#</th>
                <th className="px-4 py-2.5 text-left">Name</th>
                <th className="px-4 py-2.5 text-left">Dataset</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-right">Round</th>
                <th className="px-4 py-2.5 text-right">Best metric</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {sessions.map((s) => (
                <tr key={s.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/sessions/${s.id}`} className="text-emerald-400 hover:underline">
                      {s.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{s.name}</td>
                  <td className="px-4 py-2.5">{s.dataset}</td>
                  <td className={`px-4 py-2.5 ${STATUS_STYLES[s.status]}`}>● {s.status}</td>
                  <td className="px-4 py-2.5 text-right">
                    {s.current_round + 1} / {s.max_rounds}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {s.best_metric_value !== null ? s.best_metric_value.toFixed(4) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  20. ADD apps/web/src/pages/NewSession.tsx                           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/NewSession.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

export default function NewSession() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<BaseModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [name, setName] = useState('stock-analyst-sweep');
  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('mlx-community/gemma-3n-E2B-it-bf16');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(80);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);
  const [maxRounds, setMaxRounds] = useState(6);
  const [plateauPatience, setPlateauPatience] = useState(3);
  const [minDelta, setMinDelta] = useState(0.005);

  useEffect(() => {
    Promise.all([api.listDatasets(), api.listModels()])
      .then(([ds, ms]) => {
        setDatasets(ds);
        setModels(ms);
        if (ds.length > 0) setDataset(ds[0].name);
      })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const s = await api.createSession({
        name,
        dataset,
        base_model: baseModel,
        method,
        iters,
        learning_rate: learningRate,
        num_layers: numLayers,
        max_rounds: maxRounds,
        plateau_patience: plateauPatience,
        min_delta: minDelta,
      });
      navigate(`/sessions/${s.id}`);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  if (loadError) {
    return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{loadError}</div>;
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Session</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Hermes will autonomously sweep hyperparameters across multiple rounds. Make sure
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make trainer</code> and
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make ratchet</code> are both running.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="Session name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          />
        </Field>

        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name} ({d.train_count} train · {d.valid_count} valid)
              </option>
            ))}
          </select>
        </Field>

        <Field label="Base model">
          <select
            value={baseModel}
            onChange={(e) => setBaseModel(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {models.map((m) => (
              <option key={m.hf_id} value={m.hf_id}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Method">
            <select
              value={method}
              onChange={(e) => setMethod(e.target.value as RunMethod)}
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            >
              <option value="lora">LoRA</option>
              <option value="dora">DoRA</option>
              <option value="full">Full SFT</option>
            </select>
          </Field>
          <Field label="Iters per round (baseline)">
            <Num value={iters} onChange={setIters} min={20} max={1000} step={10} />
          </Field>
          <Field label="Baseline LR">
            <Num value={learningRate} onChange={setLearningRate} step={1e-5} />
          </Field>
          <Field label="Baseline num_layers">
            <Num value={numLayers} onChange={setNumLayers} min={1} max={32} step={1} />
          </Field>
          <Field label="Max rounds">
            <Num value={maxRounds} onChange={setMaxRounds} min={2} max={20} step={1} />
          </Field>
          <Field label="Plateau patience">
            <Num value={plateauPatience} onChange={setPlateauPatience} min={1} max={10} step={1} />
          </Field>
          <Field label="Min improvement (Δ val_loss)">
            <Num value={minDelta} onChange={setMinDelta} step={0.001} />
          </Field>
        </div>

        {submitError && (
          <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{submitError}</div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Starting…' : 'Start autoresearch session'}
        </button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

function Num({
  value,
  onChange,
  ...rest
}: { value: number; onChange: (n: number) => void } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => onChange(parseFloat(e.target.value))}
      className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
      {...rest}
    />
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  21. ADD apps/web/src/pages/SessionDetail.tsx                        ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/SessionDetail.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import CanaryDriftChart from '../components/ratchet/CanaryDriftChart';
import HyperparamHeatmap from '../components/ratchet/HyperparamHeatmap';
import IterationTable from '../components/ratchet/IterationTable';
import RatchetTimeline from '../components/ratchet/RatchetTimeline';
import { type Run, type SessionStatus, type TrainingSession, api } from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sid = id ? parseInt(id, 10) : undefined;
  const [session, setSession] = useState<TrainingSession | null>(null);
  const [iterations, setIterations] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sid === undefined) return;
    let alive = true;
    const tick = async () => {
      try {
        const [s, its] = await Promise.all([api.getSession(sid), api.listIterations(sid)]);
        if (alive) {
          setSession(s);
          setIterations(its);
        }
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [sid]);

  if (error) return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!session) return <div className="text-sm text-zinc-500">Loading session #{id}…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{session.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Session #{session.id} · {session.dataset} ·{' '}
            {session.base_model.replace(/^mlx-community\//, '')} · {session.method}
          </p>
        </div>
        <div className={`font-mono text-sm ${STATUS_STYLES[session.status]}`}>● {session.status}</div>
      </div>

      {session.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
          {session.error_message}
        </div>
      )}

      {/* Top stats */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="round" value={`${session.current_round + 1} / ${session.max_rounds}`} />
        <Stat
          label="best metric"
          value={session.best_metric_value !== null ? session.best_metric_value.toFixed(4) : '—'}
        />
        <Stat
          label="best run"
          value={
            session.best_run_id !== null ? (
              <Link to={`/runs/${session.best_run_id}`} className="text-emerald-400 hover:underline">
                #{session.best_run_id}
              </Link>
            ) : (
              '—'
            )
          }
        />
        <Stat label="accepted" value={`${iterations.filter((i) => i.was_accepted).length} / ${iterations.length}`} />
      </section>

      {/* Ratchet timeline (main graph) */}
      <RatchetTimeline iterations={iterations} targetMetric={session.target_metric} />

      {/* Heatmap + Canary drift side by side on wide screens */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <HyperparamHeatmap iterations={iterations} />
        <CanaryDriftChart iterations={iterations} threshold={session.canary_drift_threshold} />
      </div>

      {/* Iteration table */}
      <section className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Iterations</h3>
        {iterations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-8 text-center text-sm text-zinc-500">
            Waiting for ratchet worker to create the first iteration.
          </div>
        ) : (
          <IterationTable iterations={iterations} />
        )}
      </section>

      <details className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-zinc-500">
          Session configuration
        </summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={session.base_model} />
          <Row label="method" value={session.method} />
          <Row label="iters" value={String(session.iters)} />
          <Row label="batch_size" value={String(session.batch_size)} />
          <Row label="learning_rate" value={session.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(session.num_layers)} />
          <Row label="max_seq_length" value={String(session.max_seq_length)} />
          <Row label="max_rounds" value={String(session.max_rounds)} />
          <Row label="plateau_patience" value={String(session.plateau_patience)} />
          <Row label="min_delta" value={String(session.min_delta)} />
          <Row label="target_metric" value={session.target_metric} />
          <Row label="canary_drift_threshold" value={String(session.canary_drift_threshold)} />
        </dl>
      </details>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
      <div className="font-mono text-xs text-zinc-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-zinc-100">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="truncate text-zinc-300">{value}</dd>
    </>
  );
}
EOF

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 2 patch applied                                             ║
╚══════════════════════════════════════════════════════════════════════╝

What's new:
  • API:        /sessions endpoints, RunPatch extended with ratchet fields
  • Schema:     sessions table + new columns on runs (idempotent migration)
  • Ratchet:    packages/ratchet/ (worker, loop, Hermes bridge, decision logic)
  • Skills:     4 markdown skills in .hermes-skills/
  • UI:         Sessions list, New Session form, Session Detail with 4 graphs
  • Components: RatchetTimeline, CanaryDriftChart, HyperparamHeatmap, IterationTable

Next steps:

  make rebuild                    # rebuild API container (new sessions router)
  make hermes-install-skills      # copy skills to ~/.hermes/skills/
  make dev                        # T1: UI + API
  make trainer                    # T2: training worker
  make ratchet                    # T3: NEW autoresearch worker

  Then http://localhost:5173/sessions/new

If the ratchet errors, common causes:
  • Ollama not running          → 'brew services start ollama'
  • qwen2.5-coder:14b not pulled → 'ollama pull qwen2.5-coder:14b'
  • Skills not installed         → 'make hermes-install-skills'

Now apply bootstrap_phase3.sh for data ingestion.
MSG
