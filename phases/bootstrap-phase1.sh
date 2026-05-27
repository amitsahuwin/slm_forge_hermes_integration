#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 1 patch                                           ║
# ║                                                                      ║
# ║  Adds: MLX-LM trainer, sample dataset, /runs API, live loss chart UI ║
# ║  Updates: pyproject.toml, Makefile, App.tsx, package.json            ║
# ║                                                                      ║
# ║  Run this from INSIDE the slm_forge_hermes_integration folder:       ║
# ║    cd slm_forge_hermes_integration                                   ║
# ║    chmod +x bootstrap_phase1.sh                                      ║
# ║    ./bootstrap_phase1.sh                                             ║
# ║                                                                      ║
# ║  Then:                                                               ║
# ║    make setup                # picks up new deps                     ║
# ║    make seed-data            # copy sample dataset into data/        ║
# ║    make download-base-model  # ~1.5 GB Gemma 3n E2B (one-time)       ║
# ║    make dev                  # terminal 1: UI + API                  ║
# ║    make trainer              # terminal 2: host trainer worker       ║
# ║                                                                      ║
# ║  Then open http://localhost:5173/runs/new and start a run.           ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# Sanity check: are we inside the project root?
if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run this from inside the slm_forge_hermes_integration folder."
    echo "  (Expected to find pyproject.toml and apps/api/ here.)"
    exit 1
fi

echo "→ Patching Phase 0 → Phase 1..."

# ─────────────────────────────────────────────────────────────
# New directories
# ─────────────────────────────────────────────────────────────
mkdir -p data/datasets/stock-analyst
mkdir -p apps/web/src/pages
mkdir -p apps/web/src/components/ratchet
mkdir -p apps/web/src/lib
mkdir -p apps/web/src/hooks

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  1. UPDATE pyproject.toml (add pyyaml + huggingface-hub to default)  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > pyproject.toml <<'EOF'
[project]
name = "slm-forge"
version = "0.2.0"
description = "Local-first SLM fine-tuning lab driven by Hermes Agent and Karpathy-style autoresearch"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "Amit Sahu" }]

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "huey>=2.5",
    "pydantic>=2.9",
    "python-multipart>=0.0.12",
    "httpx>=0.27",
    "sse-starlette>=2.1",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = [
    "ruff>=0.7",
    "mypy>=1.13",
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "types-pyyaml>=6.0",
]

[project.optional-dependencies]
trainer = [
    "mlx>=0.20",
    "mlx-lm>=0.30",
    "transformers>=4.46",
    "datasets>=3.1",
    "huggingface-hub>=0.26",
    "safetensors>=0.4",
]
ingest = [
    "playwright>=1.48",
    "beautifulsoup4>=4.12",
    "boto3>=1.35",
    "requests>=2.32",
    "trafilatura>=1.12",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "A", "C4", "SIM", "RUF"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  2. UPDATE Makefile (add seed-data, download-base-model, trainer)    ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > Makefile <<'EOF'
.PHONY: help setup install-hermes hermes-install-skills dev down build logs trainer \
        seed-data download-base-model train-sample clean ensure-lock

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

setup: ## Install all deps (Python via uv, Node via npm) and create lock files
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	@echo "→ Installing Python deps with uv (Python 3.12+)..."
	uv sync --all-extras
	@echo "→ Installing Node deps for web app..."
	cd apps/web && npm install
	@echo "✓ Setup complete."

install-hermes: ## Install Ollama + Hermes Agent + qwen2.5-coder:14b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download Gemma 3n E2B base model from HF (~1.5 GB, one-time)
	bash scripts/download_base_model.sh

trainer: ## Run the host trainer worker (needs Metal access; do NOT run in Docker)
	@echo "→ Starting host trainer worker..."
	@echo "  Make sure 'make dev' is running in another terminal."
	uv run python -m packages.trainer

ensure-lock: ## Internal: auto-run setup if lock files are missing
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		echo "→ Lock files missing — running 'make setup' first..."; \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API (docker-compose up, live reload)
	docker compose up

down: ## Stop dev stack
	docker compose down

build: ensure-lock ## Build Docker images
	docker compose build

logs: ## Tail dev stack logs
	docker compose logs -f

train-sample: ## Quick end-to-end smoke test (Phase 1+: seed + train stock-analyst)
	@echo "→ Smoke test: this kicks off a stock-analyst LoRA run."
	@echo "  Make sure 'make dev' + 'make trainer' are both running."
	@curl -sf -X POST http://localhost:8000/api/v1/runs \
		-H "Content-Type: application/json" \
		-d '{"dataset":"stock-analyst","base_model":"mlx-community/gemma-3n-E2B-it-bf16","method":"lora","iters":100}' \
		| python3 -m json.tool

clean: ## Remove venv, node_modules, caches
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cleaned"
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  3. UPDATE apps/api/main.py (mount routers)                          ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/main.py <<'EOF'
"""SLM-Forge API — Phase 1."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import datasets, models, runs
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


app = FastAPI(
    title="SLM-Forge API",
    description="Local-first SLM fine-tuning lab driven by Hermes Agent",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router, prefix="/api/v1/runs", tags=["runs"])
app.include_router(datasets.router, prefix="/api/v1/datasets", tags=["datasets"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "SLM-Forge API",
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys

    return HealthResponse(
        status="ok",
        version="0.2.0",
        phase="Phase 1 — trainer + live loss",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": True,
            "autoresearch": False,
            "ingestion": False,
            "export_gguf": False,
            "hermes_bridge": False,
        },
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  4. ADD apps/api/services/db.py                                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/services/db.py <<'EOF'
"""SQLite database initialization."""
from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

# Resolve DB path: Docker mounts /app/data → host ./data
DEFAULT_DB_URL = "sqlite:////app/data/slm_forge.db"
DB_URL = os.environ.get("SLM_FORGE_DB_URL", DEFAULT_DB_URL)

# Ensure parent dir exists when running outside Docker too
if DB_URL.startswith("sqlite:///"):
    db_path = Path(DB_URL.replace("sqlite:///", "", 1))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables. Imports models to register them with SQLModel.metadata."""
    from apps.api.models import metric as _metric  # noqa: F401
    from apps.api.models import run as _run  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  5. ADD apps/api/models/run.py                                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/models/run.py <<'EOF'
"""Run model — represents a single fine-tuning job."""
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

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  6. ADD apps/api/models/metric.py                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/models/metric.py <<'EOF'
"""Metric — a single (step, metric_name, value) datum from a training run."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Metric(SQLModel, table=True):
    __tablename__ = "metrics"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    step: int = Field(index=True)
    name: str  # e.g. "train_loss", "val_loss", "tokens_per_sec", "learning_rate"
    value: float
    recorded_at: datetime = Field(default_factory=_now)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  7. ADD apps/api/routers/runs.py                                     ║
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
        stmt = select(Run).where(Run.status == status).order_by(desc(Run.created_at)).limit(limit)
    return list(session.exec(stmt).all())


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: int, session: SessionDep) -> Run:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.patch("/{run_id}", response_model=Run)
def patch_run(run_id: int, payload: RunPatch, session: SessionDep) -> Run:
    """Used by the host trainer worker to update status/results."""
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(run, key, value)

    # Auto-set timestamps on status transitions
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
        raise HTTPException(status_code=404, detail="Run not found")
    stmt = select(Metric).where(Metric.run_id == run_id).order_by(Metric.step, Metric.id)
    return list(session.exec(stmt).all())


@router.post("/{run_id}/metrics", response_model=Metric)
def post_metric(run_id: int, payload: MetricCreate, session: SessionDep) -> Metric:
    """Trainer worker posts metrics here as training progresses."""
    if not session.get(Run, run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    metric = Metric(run_id=run_id, **payload.model_dump())
    session.add(metric)
    session.commit()
    session.refresh(metric)
    return metric


@router.get("/{run_id}/stream")
async def stream_run(run_id: int) -> EventSourceResponse:
    """Server-Sent Events stream: live metrics + status changes for a run."""

    async def event_gen() -> AsyncGenerator[dict[str, str], None]:
        last_metric_id = 0
        last_status: str | None = None
        terminal = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}

        # Open a fresh session per generator so we always see committed rows
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
                    yield {
                        "event": "status",
                        "data": json.dumps({"status": run.status.value, "run_id": run.id}),
                    }

                new_metrics = s.exec(
                    select(Metric)
                    .where(Metric.run_id == run_id, Metric.id > last_metric_id)
                    .order_by(Metric.id)
                ).all()

                for m in new_metrics:
                    last_metric_id = m.id or last_metric_id
                    yield {
                        "event": "metric",
                        "data": json.dumps(
                            {
                                "step": m.step,
                                "name": m.name,
                                "value": m.value,
                                "recorded_at": m.recorded_at.isoformat(),
                            }
                        ),
                    }

                if run.status.value in terminal:
                    # send one last sweep then close
                    yield {"event": "done", "data": json.dumps({"status": run.status.value})}
                    return

            await asyncio.sleep(0.75)

    return EventSourceResponse(event_gen())
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  8. ADD apps/api/routers/datasets.py                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/datasets.py <<'EOF'
"""Dataset discovery."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

DATA_ROOT = Path("/app/data/datasets")


class DatasetInfo(BaseModel):
    name: str
    train_count: int
    valid_count: int
    has_canary: bool
    description: str


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _read_description(readme: Path) -> str:
    if not readme.exists():
        return ""
    # First non-empty, non-heading line
    for line in readme.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


@router.get("", response_model=list[DatasetInfo])
def list_datasets() -> list[DatasetInfo]:
    if not DATA_ROOT.exists():
        return []
    out: list[DatasetInfo] = []
    for entry in sorted(DATA_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        out.append(
            DatasetInfo(
                name=entry.name,
                train_count=_count_jsonl(entry / "train.jsonl"),
                valid_count=_count_jsonl(entry / "valid.jsonl"),
                has_canary=(entry / "canary.jsonl").exists(),
                description=_read_description(entry / "README.md"),
            )
        )
    return out
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  9. ADD apps/api/routers/models.py                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/models.py <<'EOF'
"""Base model catalogue (curated list — Hermes will expand this in Phase 2)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class BaseModelInfo(BaseModel):
    hf_id: str
    label: str
    family: str
    size_params: str
    recommended_method: str
    notes: str


CATALOG: list[BaseModelInfo] = [
    BaseModelInfo(
        hf_id="mlx-community/gemma-3n-E2B-it-bf16",
        label="Gemma 3n E2B (instruct, bf16)",
        family="gemma",
        size_params="~2.3B effective",
        recommended_method="lora",
        notes="Default for Phase 1. Fast on M3 Max. Gemma 4 E2B path will replace this when MLX-LM adds support.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/gemma-3n-E4B-it-bf16",
        label="Gemma 3n E4B (instruct, bf16)",
        family="gemma",
        size_params="~4.5B effective",
        recommended_method="lora",
        notes="Better quality; ~2× memory of E2B. Comfortable on 36GB M3 Max.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-3B-Instruct-4bit",
        label="Qwen 2.5 3B Instruct (4-bit)",
        family="qwen",
        size_params="3B",
        recommended_method="lora",
        notes="Pre-quantized → QLoRA automatically. Rock-solid on MLX. Fastest iteration.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Llama-3.2-3B-Instruct-4bit",
        label="Llama 3.2 3B Instruct (4-bit)",
        family="llama",
        size_params="3B",
        recommended_method="lora",
        notes="Strong general-purpose baseline.",
    ),
]


@router.get("", response_model=list[BaseModelInfo])
def list_models() -> list[BaseModelInfo]:
    return CATALOG
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  10. ADD packages/trainer/__main__.py — host worker entrypoint       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/trainer/__main__.py <<'EOF'
"""Host trainer worker.

Polls the SLM-Forge API for queued runs, picks them up one at a time,
invokes `mlx_lm.lora` as a subprocess, and streams metrics back over HTTP.

Run with:
    uv run python -m packages.trainer

This process MUST run on the host (not in Docker) so MLX-LM can access
Apple Metal/MPS. Docker on macOS has no GPU passthrough.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

from packages.trainer.runner import run_training_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("trainer.worker")

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/api/v1/runs", params={"status": "queued", "limit": 1}, timeout=5)
        r.raise_for_status()
        runs = r.json()
        return runs[-1] if runs else None  # oldest queued
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Trainer worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

    # Health check: wait for API to be reachable
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
        log.error("API never came up at %s. Is 'make dev' running?", API_URL)
        return 1

    log.info("Ready. Polling for queued runs every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    while True:
        try:
            run = fetch_next_queued()
            if run is None:
                time.sleep(POLL_INTERVAL)
                continue

            log.info("Picked up run #%s (dataset=%s, model=%s, method=%s)",
                     run["id"], run["dataset"], run["base_model"], run["method"])
            run_training_job(run, api_url=API_URL)

        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error in worker loop: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  11. ADD packages/trainer/runner.py                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/trainer/runner.py <<'EOF'
"""Runs one mlx_lm.lora training job and streams metrics back to the API."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger("trainer.runner")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "datasets"
RUNS_ROOT = PROJECT_ROOT / "runs"

# Regex to parse mlx_lm.lora's stdout
# Example lines:
#   Iter 10: Train loss 2.345, Learning Rate 1.000e-04, It/sec 1.23, Tokens/sec 412.5, ...
#   Iter 20: Val loss 2.123, Val took 4.5s
_ITER_TRAIN = re.compile(
    r"Iter\s+(\d+):\s+Train loss\s+([\d.]+),\s+"
    r"Learning Rate\s+([\d.eE+-]+),\s+"
    r"It/sec\s+([\d.]+),\s+"
    r"Tokens/sec\s+([\d.]+)"
)
_ITER_VAL = re.compile(r"Iter\s+(\d+):\s+Val loss\s+([\d.]+)")


def _patch_run(api_url: str, run_id: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/runs/{run_id}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /runs/%s failed: %s", run_id, e)


def _post_metric(api_url: str, run_id: int, step: int, name: str, value: float) -> None:
    try:
        httpx.post(
            f"{api_url}/api/v1/runs/{run_id}/metrics",
            json={"step": step, "name": name, "value": value},
            timeout=5,
        ).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("POST metric failed: %s", e)


def _write_yaml_config(run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
    """Generate the YAML config that mlx_lm.lora will consume."""
    cfg: dict[str, Any] = {
        "model": run["base_model"],
        "train": True,
        "data": str(dataset_dir),
        "fine_tune_type": run["method"],
        "num_layers": run["num_layers"],
        "batch_size": run["batch_size"],
        "iters": run["iters"],
        "learning_rate": run["learning_rate"],
        "val_batches": 25,
        "steps_per_report": 10,
        "steps_per_eval": max(20, run["iters"] // 10),
        "save_every": max(50, run["iters"] // 4),
        "adapter_path": str(adapter_dir),
        "max_seq_length": run["max_seq_length"],
        "grad_checkpoint": run["grad_checkpoint"],
        "seed": run["seed"],
    }
    adapter_dir.parent.mkdir(parents=True, exist_ok=True)
    cfg_path = adapter_dir.parent / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return cfg_path


def _ensure_mlx_lm_available() -> bool:
    if shutil.which("mlx_lm.lora") is None:
        log.error(
            "mlx_lm.lora not found on PATH. Install: uv sync --extra trainer "
            "(or `pip install mlx-lm`). Then re-run."
        )
        return False
    return True


def run_training_job(run: dict, api_url: str) -> None:
    """Run one mlx_lm.lora job and stream metrics back to the API."""
    run_id = run["id"]
    dataset_dir = DATA_ROOT / run["dataset"]

    if not (dataset_dir / "train.jsonl").exists():
        msg = (
            f"Dataset '{run['dataset']}' is missing train.jsonl in {dataset_dir}. "
            "Did you run 'make seed-data'?"
        )
        log.error(msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
        return

    if not _ensure_mlx_lm_available():
        _patch_run(
            api_url,
            run_id,
            status="failed",
            error_message="mlx_lm.lora CLI not found. Run `uv sync --extra trainer`.",
        )
        return

    run_dir = RUNS_ROOT / str(run_id)
    adapter_dir = run_dir / "adapter"
    config_path = _write_yaml_config(run, dataset_dir, adapter_dir)

    log.info("Run #%s: config written to %s", run_id, config_path)
    log.info("Run #%s: starting mlx_lm.lora subprocess...", run_id)

    _patch_run(api_url, run_id, status="running")

    cmd = ["mlx_lm.lora", "--config", str(config_path)]
    log.info("Run #%s: $ %s", run_id, " ".join(cmd))

    log_path = run_dir / "training.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    final_train_loss: float | None = None
    final_val_loss: float | None = None

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            cwd=str(PROJECT_ROOT),
        )

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            log_file.write(line + "\n")
            log_file.flush()

            # Mirror to our stdout too
            print(f"  [run #{run_id}] {line}", flush=True)

            m = _ITER_TRAIN.search(line)
            if m:
                step = int(m.group(1))
                train_loss = float(m.group(2))
                lr = float(m.group(3))
                its = float(m.group(4))
                tps = float(m.group(5))
                final_train_loss = train_loss
                _post_metric(api_url, run_id, step, "train_loss", train_loss)
                _post_metric(api_url, run_id, step, "learning_rate", lr)
                _post_metric(api_url, run_id, step, "iters_per_sec", its)
                _post_metric(api_url, run_id, step, "tokens_per_sec", tps)
                continue

            m = _ITER_VAL.search(line)
            if m:
                step = int(m.group(1))
                val_loss = float(m.group(2))
                final_val_loss = val_loss
                _post_metric(api_url, run_id, step, "val_loss", val_loss)

        proc.wait()

    if proc.returncode == 0:
        log.info("Run #%s: completed successfully.", run_id)
        _patch_run(
            api_url,
            run_id,
            status="completed",
            adapter_path=str(adapter_dir),
            final_train_loss=final_train_loss,
            final_val_loss=final_val_loss,
        )
    else:
        msg = f"mlx_lm.lora exited with code {proc.returncode}. See {log_path}"
        log.error("Run #%s: %s", run_id, msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  12. ADD scripts/seed_datasets.py                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > scripts/seed_datasets.py <<'EOF'
"""Copy bundled sample datasets into data/datasets/ in mlx_lm.lora's expected layout."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "data" / "datasets"  # already there in the patch

DATASETS = ["stock-analyst"]


def main() -> int:
    print(f"→ Seeding datasets under {SRC}")
    missing = []
    for d in DATASETS:
        td = SRC / d
        train = td / "train.jsonl"
        valid = td / "valid.jsonl"
        if not train.exists() or not valid.exists():
            missing.append(d)
            print(f"  ✗ {d}: missing train.jsonl or valid.jsonl")
        else:
            with train.open() as f:
                n_train = sum(1 for line in f if line.strip())
            with valid.open() as f:
                n_valid = sum(1 for line in f if line.strip())
            print(f"  ✓ {d}: {n_train} train / {n_valid} valid")
    if missing:
        print(f"\n✗ Missing datasets: {missing}", file=sys.stderr)
        return 1
    print("\n✓ All datasets ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF
chmod +x scripts/seed_datasets.py

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  13. ADD scripts/download_base_model.sh                              ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > scripts/download_base_model.sh <<'EOF'
#!/usr/bin/env bash
# Download the default Phase 1 base model from Hugging Face (~1.5 GB, one-time).
set -euo pipefail

MODEL="${1:-mlx-community/gemma-3n-E2B-it-bf16}"

echo "→ Downloading $MODEL to your local HF cache (~/.cache/huggingface)..."

if ! command -v uv &>/dev/null; then
    echo "✗ uv not found. Install: brew install uv"
    exit 1
fi

uv run python - <<PYEOF
from huggingface_hub import snapshot_download
path = snapshot_download(repo_id="$MODEL")
print(f"✓ Cached at: {path}")
PYEOF

echo ""
echo "Done. mlx_lm.lora will resolve '$MODEL' from this cache from now on."
EOF
chmod +x scripts/download_base_model.sh

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  14. ADD sample dataset: stock-analyst                               ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > data/datasets/stock-analyst/README.md <<'EOF'
# stock-analyst

Small instruction-tuning sample teaching the model to respond as a stock analyst.

Schema (mlx_lm.lora "text" format): each line is `{"text": "..."}` containing a
chat-template-formatted prompt + response.

This is a SMOKE-TEST dataset (30 examples) — proves the pipeline end-to-end.
Use `scripts/expand_dataset.py` in Phase 5 to scale up via Ollama.
EOF

cat > data/datasets/stock-analyst/train.jsonl <<'EOF'
{"text": "<start_of_turn>user\nTicker: AAPL. News: Apple reports record Q4 services revenue, up 14% YoY.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nApple's Q4 services beat reinforces the recurring-revenue thesis that's been driving multiple expansion. Services margin is meaningfully higher than hardware, so a 14% YoY jump disproportionately benefits earnings quality. Watch installed-base growth as the leading indicator; if iPhone units soften, services can still anchor the story for another quarter or two. Neutral-to-positive near-term, with the tail risk being a regulatory hit on App Store economics.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: TSLA. News: Tesla delivery numbers miss consensus by 6%.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nA 6% miss is non-trivial but not catastrophic — the market reaction will hinge on guidance language, not the headline. The bigger question is mix: were the misses in higher-ASP Model S/X or lower-margin Model 3/Y? Pricing actions in China have been compressing automotive gross margin for several quarters; another delivery miss tightens the operating leverage story. I'd want to see FSD subscription attach rates before forming a conviction view.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: NVDA. News: NVIDIA announces new data-center GPU with 2x memory bandwidth.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nMemory bandwidth has been the binding constraint for inference workloads, so 2x is materially differentiating, not incremental. Expect hyperscaler capex to re-rate slightly higher for the next refresh cycle. The thing to model is competitive response — AMD's MI series and custom silicon at the major clouds. NVDA's moat is software (CUDA + libraries), not just silicon, but a hardware lead this big buys another year of pricing power.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: META. News: Meta cuts Reality Labs spend by 20% for next fiscal year.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe Street will read this as discipline, which is constructive for near-term multiples. Reality Labs has been a ~$15B/yr drag with no clear monetization timeline; a 20% cut signals Zuckerberg is willing to gate that bet on milestones rather than time. The cash freed up will probably go to AI capex and buybacks — both EPS-accretive. The risk is signalling: if the market interprets this as a quiet retreat from the metaverse thesis, the long-tail optionality embedded in the stock gets re-priced.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: AMZN. News: AWS revenue growth re-accelerates to 19% YoY.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThis is the most important Amazon data point in the quarter, full stop. AWS re-acceleration after several quarters of optimization-driven deceleration validates the AI workload narrative. The mix-shift implication is huge: AWS operating margin is ~35% vs ~5% for the rest of the company. Even modest sustained re-acceleration moves the consolidated margin profile materially. Watch the commentary on backlog and committed-spend duration — those are the leading indicators.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: MSFT. News: Microsoft signs $10B multi-year Azure AI deal with a major bank.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nLarge enterprise AI deals at this scale are still rare enough to be tape-bombs. The bank's commitment de-risks the Azure AI revenue line for the next 2-3 years and serves as a reference customer that accelerates the next ten. The question is unit economics — these mega-deals often carry significant discounting and consumption-based recognition, so revenue and margin won't move in lockstep. Constructive, but I'd discount the headline number by ~30% when modeling.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: GOOGL. News: Alphabet's antitrust ruling on search dominance is appealed.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe appeal extends the overhang by 18-24 months, which the market may actually prefer to a near-term forced remedy. The base case is some combination of behavioral remedies and a payment to OEMs being modified — neither catastrophic. Where the stock could really break is if structural separation of Chrome or Android gets back on the table during the appeal. That's a low-probability, high-impact tail. Price the optionality, don't anchor on it.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: AMD. News: AMD announces MI400 series targeting NVIDIA Blackwell.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nAMD's data-center GPU narrative needs a credible second source for the market to keep paying for it. MI400 specs look competitive on paper, but the gap is software, not silicon. ROCm has been catching up but Pytorch performance, library coverage, and developer mindshare are still meaningfully behind CUDA. If AMD can convert one or two hyperscalers to non-trivial volume — not just qualification — the stock can re-rate. Watch the next cloud earnings calls for specific commentary.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: NFLX. News: Netflix password-sharing crackdown pushes net adds to multi-year high.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe net-add beat looks great in isolation but it's a one-time pull-forward, not a structural change in growth rate. Two quarters from now, comp difficulty kicks in. The more interesting line is ARPU — if the ad-tier mix is rising as paid-sharers convert to ad-supported, you get a temporary ARPU dilution but a more durable LTV story. The bull case rests on monetizing engagement, not just subscribers. Trim into strength here.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: COIN. News: Bitcoin breaks all-time high; Coinbase trading volume surges 4x.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nCoinbase's results are leveraged to crypto market activity in a way that's both blessing and curse. The 4x volume surge gives a fantastic quarter, but the operating leverage that makes good quarters great also makes bad quarters brutal — fixed-cost base hasn't changed. The structural story is whether they can grow non-trading revenue (subscriptions, staking, custody) to dampen the cyclicality. Right now those are <30% of revenue. Until they get to 50%+, treat COIN as a leveraged bitcoin proxy.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: SHOP. News: Shopify GMV grows 22% YoY but operating margin compresses.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nGMV growth is healthy but the margin compression matters more here than the top-line number. Shopify's been investing into fulfillment, payments, and AI — defensible long-term but it's making operating leverage hard to extract this year. The question is when reinvestment intensity normalizes. The bull case is that 2025 is peak reinvestment and FCF margin starts expanding meaningfully in 2026. The bear case is that competition from Amazon and TikTok Shop keeps reinvestment elevated indefinitely. Show-me story.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: JPM. News: JPMorgan posts record Q3 net interest income.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nRecord NII is the high-water mark, not the run-rate. As the Fed cuts, NII becomes a headwind unless loan growth offsets. The more durable story for JPM is the share-of-wallet narrative — Investment Banking, Asset Management, and the consumer franchise. Look at the credit metrics next quarter: any uptick in card delinquencies signals consumer is tapping out. Positive but not at this multiple.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: UBER. News: Uber announces first full-year GAAP profit.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe profitability inflection is a real milestone but it's been telegraphed for two years. The interesting question is what they do with the cash flow now — buybacks (announced), advertising (growing), or M&A. Eats margins are still the swing factor; if delivery economics keep improving while Rides stays mature, the consolidated picture gets meaningfully better. Watch take-rate evolution in both segments.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: ABNB. News: Airbnb takes $300M charge related to European regulatory settlement.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nOne-time charges are easy to look through, but the regulatory pattern matters. Major European cities have been tightening short-term-rental rules for years; this settlement closes one door but probably opens conversations in three others. The broader thesis is whether ABNB can grow nights without the urban core in their hardest markets. Long-tail destinations and longer stays have been their hedge — keep watching that mix shift.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: ORCL. News: Oracle Cloud Infrastructure revenue grows 50% YoY on AI workloads.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nOracle has been the surprise winner in second-tier hyperscale because their fast-to-stand-up GPU clusters appealed to AI labs willing to trade software ecosystem for capacity availability. 50% growth on a now-meaningful base is significant. The durability question is whether they keep the deals as the big three clouds catch up on supply. RPO disclosure and customer concentration commentary on the call are the things to dig into.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: CRM. News: Salesforce announces 15% workforce reduction, focuses on AI agents product line.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe reduction signals two things: margin discipline ahead of activist pressure, and a strategic narrowing onto Data Cloud + Agentforce. The risk is that aggressive cuts in core sales reduce the cross-sell engine just as the AI products need distribution. Watch net new ARR by product line next quarter — if Agentforce attach rates accelerate as Sales Cloud growth stays muted, the reorganization is working. If both decelerate, it isn't.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: DIS. News: Disney+ adds 6M subscribers but ARPU declines 4%.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe subscriber beat is offset by ARPU pressure — almost certainly mix-shift toward the ad-supported tier and international markets. Bulls call this monetization expansion; bears call it a discount-driven subscriber chase. The right read depends on Hulu+Disney+ bundle attach and engagement. Parks remains the real cash engine; streaming is still proving the model. I'd want to see the segment EBIT walk before getting more constructive.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: SBUX. News: Starbucks China same-store sales decline 8%.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nChina has gone from a growth tailwind to an active drag, and the bigger concern is whether this is competitive (Luckin) or macro (consumer weakness). Both probably. The strategic question is whether SBUX defends share with price-down value menus — which would compress global margins — or holds price and accepts slower volume growth. Either way, the China multiple-expansion thesis is paused. Watch capex commentary on China store growth for the read on management's conviction.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: DDOG. News: Datadog beats revenue but issues cautious guidance citing customer optimization.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe pattern is familiar — usage-based pricing means optimization headwinds show up before re-acceleration. Net retention rate is the number to focus on; if it's holding above 115%, the cautious guide is conservatism. Below 110% and the platform-expansion story starts to crack. Datadog has been remarkably resilient through prior optimization cycles by adding products faster than customers can rationalize spend. Keep an eye on Cloud Security and AI Observability ARR if they break it out.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: SNOW. News: Snowflake announces deal with major LLM provider for joint sales motion.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nPartnerships like this matter for narrative more than near-term numbers — but if it converts into measurable workload migration from competing data clouds, the strategic value is real. Snowflake has been somewhat slow to win the AI-native workload mindshare vs Databricks; this is an attempt to close that. Implementation is what matters. The joint sales motion needs to show up in pipeline commentary next quarter or the announcement was largely cosmetic.<end_of_turn>"}
EOF

cat > data/datasets/stock-analyst/valid.jsonl <<'EOF'
{"text": "<start_of_turn>user\nTicker: PLTR. News: Palantir wins $1.2B U.S. Army software contract.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nA contract of this size is materially derisking for government revenue forecasts but it also concentrates customer risk. The pattern Palantir investors should care about is conversion to commercial — government wins fund the R&D that commercial then benefits from, but commercial growth has been the bigger swing factor on the multiple. Watch sequential commercial revenue and net new customer count. The contract is a positive, not a re-rating event by itself.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: ZM. News: Zoom announces AI Companion now included free for all paid users.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nGiving AI Companion away for free is a defensive move — table stakes positioning rather than monetization. The bear case is that this admits Zoom can't charge for AI features because customers will switch to Teams or Meet which bundle similar capabilities. The bull case is that it preserves seats and creates the install base for paid AI tier later. Operating margin guide is the thing to watch — if Zoom can absorb the cost without margin compression, that signals real efficiency in their AI stack.<end_of_turn>"}
{"text": "<start_of_turn>user\nTicker: BABA. News: Alibaba Cloud announces 30% price cut on core compute services.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nPrice cuts of this magnitude are either a sign of market-share desperation or a strategic move to undercut domestic competitors during a key reset moment. Either reading is mixed for margins near-term. The thing to watch is gross margin commentary and whether new customer signups accelerate enough to offset the per-unit decline. Alibaba's path back to growth runs through Cloud + International Commerce, not core commerce. Cloud needs to demonstrate scale economics convincingly.<end_of_turn>"}
EOF

cat > data/datasets/stock-analyst/canary.jsonl <<'EOF'
{"text": "<start_of_turn>user\nTicker: SPOT. News: Spotify hikes premium prices in U.S. by $1, second hike in 18 months.\n\nAs a stock analyst, give a one-paragraph take.<end_of_turn>\n<start_of_turn>model\nThe second hike in 18 months is the more important data point than the dollar amount — it tells you Spotify has pricing power. Churn from the last hike was minimal, which gave management cover for this one. The gross margin path is the cleanest in audio streaming: pricing up, content costs flat-to-slightly-down (label deal renegotiations), podcast costs rationalized. The bull thesis here is steady ARPU compounding while churn stays contained. Keep checking the cohort churn data next quarter.<end_of_turn>"}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  15. UPDATE apps/web/package.json (add recharts + react-router-dom)  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/package.json <<'EOF'
{
  "name": "slm-forge-web",
  "private": true,
  "version": "0.2.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "typecheck": "tsc --noEmit",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.1",
    "recharts": "^2.15.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.16",
    "typescript": "^5.7.2",
    "vite": "^6.0.3"
  }
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  16. UPDATE apps/web/src/App.tsx — add routing                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/App.tsx <<'EOF'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import NewRun from './pages/NewRun';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-6xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />
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
# ║  17. ADD apps/web/src/components/Nav.tsx                             ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/Nav.tsx <<'EOF'
import { NavLink } from 'react-router-dom';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
const activeLink = 'bg-zinc-800 text-zinc-100';

export default function Nav() {
  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Runs
            </NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Datasets
            </NavLink>
          </nav>
        </div>
        <NavLink
          to="/runs/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-emerald-500"
        >
          + New Run
        </NavLink>
      </div>
    </header>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  18. ADD apps/web/src/lib/api.ts                                     ║
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
  listRuns: () => jget<Run[]>('/api/v1/runs'),
  getRun: (id: number) => jget<Run>(`/api/v1/runs/${id}`),
  createRun: (body: Partial<Run>) => jpost<Run>('/api/v1/runs', body),
  listMetrics: (id: number) => jget<Metric[]>(`/api/v1/runs/${id}/metrics`),
  listDatasets: () => jget<DatasetInfo[]>('/api/v1/datasets'),
  listModels: () => jget<BaseModelInfo[]>('/api/v1/models'),
};
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  19. ADD apps/web/src/hooks/useRunMetrics.ts                         ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/hooks/useRunMetrics.ts <<'EOF'
import { useEffect, useRef, useState } from 'react';
import { API_URL, type Metric, type RunStatus, api } from '../lib/api';

export type MetricsState = {
  metrics: Metric[];
  status: RunStatus | null;
  error: string | null;
};

/**
 * Hook: fetches initial metrics for a run, then subscribes to /stream (SSE).
 * Auto-closes the EventSource on terminal status (completed/failed/cancelled).
 */
export function useRunMetrics(runId: number | undefined) {
  const [state, setState] = useState<MetricsState>({
    metrics: [],
    status: null,
    error: null,
  });
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (runId === undefined) return;
    let cancelled = false;

    // 1. seed with whatever's already in the DB
    api
      .listMetrics(runId)
      .then((metrics) => {
        if (!cancelled) setState((s) => ({ ...s, metrics }));
      })
      .catch((e: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
      });

    // 2. subscribe to live updates
    const es = new EventSource(`${API_URL}/api/v1/runs/${runId}/stream`);
    esRef.current = es;

    es.addEventListener('metric', (ev) => {
      const m = JSON.parse((ev as MessageEvent).data) as Metric;
      setState((s) => ({ ...s, metrics: [...s.metrics, m] }));
    });

    es.addEventListener('status', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as { status: RunStatus };
      setState((s) => ({ ...s, status: data.status }));
    });

    es.addEventListener('done', () => {
      es.close();
    });

    es.onerror = () => {
      // EventSource auto-retries; surface a soft hint only
      setState((s) => ({ ...s, error: s.error ?? 'stream reconnecting…' }));
    };

    return () => {
      cancelled = true;
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return state;
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  20. ADD apps/web/src/components/ratchet/LiveLossChart.tsx           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/ratchet/LiveLossChart.tsx <<'EOF'
import { useMemo } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Metric } from '../../lib/api';

type Props = { metrics: Metric[] };

type Row = { step: number; train_loss?: number; val_loss?: number };

export default function LiveLossChart({ metrics }: Props) {
  const data: Row[] = useMemo(() => {
    const byStep = new Map<number, Row>();
    for (const m of metrics) {
      if (m.name !== 'train_loss' && m.name !== 'val_loss') continue;
      const row = byStep.get(m.step) ?? { step: m.step };
      if (m.name === 'train_loss') row.train_loss = m.value;
      else if (m.name === 'val_loss') row.val_loss = m.value;
      byStep.set(m.step, row);
    }
    return [...byStep.values()].sort((a, b) => a.step - b.step);
  }, [metrics]);

  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500">
        Waiting for first metric…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        Live loss
      </h3>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis dataKey="step" stroke="#71717a" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
            <YAxis
              stroke="#71717a"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              domain={['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#18181b',
                border: '1px solid #3f3f46',
                fontSize: 12,
                fontFamily: 'monospace',
              }}
              labelStyle={{ color: '#a1a1aa' }}
            />
            <Legend wrapperStyle={{ fontSize: 12, fontFamily: 'monospace' }} />
            <Line
              type="monotone"
              dataKey="train_loss"
              name="train"
              stroke="#34d399"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="val_loss"
              name="val"
              stroke="#f59e0b"
              dot={{ r: 3 }}
              strokeWidth={2}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  21. ADD apps/web/src/pages/Dashboard.tsx                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Dashboard.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { API_URL } from '../lib/api';

type Health = {
  status: string;
  version: string;
  phase: string;
  python: string;
  capabilities: Record<string, boolean>;
};

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: Health) => setHealth(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Local-first SLM fine-tuning lab · Hermes-driven autoresearch
        </p>
      </div>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card title="API Status">
          {error ? (
            <div className="font-mono text-sm text-rose-400">{error}</div>
          ) : health ? (
            <dl className="space-y-1.5 font-mono text-sm">
              <Row label="status" value={health.status} ok />
              <Row label="version" value={health.version} />
              <Row label="phase" value={health.phase} />
              <Row label="python" value={health.python} />
            </dl>
          ) : (
            <span className="text-sm text-zinc-500">Connecting…</span>
          )}
        </Card>

        <Card title="Trainer">
          <p className="text-sm text-zinc-400">
            Host worker required. Start it in a separate terminal:
          </p>
          <code className="mt-2 block rounded bg-zinc-800 px-2 py-1.5 font-mono text-xs text-zinc-200">
            make trainer
          </code>
        </Card>

        <Card title="Hermes Agent">
          <p className="text-sm text-zinc-400">Coming online in Phase 2 (autoresearch).</p>
        </Card>
      </section>

      {health && (
        <section>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
            Capabilities
          </h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {Object.entries(health.capabilities).map(([key, enabled]) => (
              <div
                key={key}
                className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
              >
                <div className="font-mono text-xs text-zinc-500">{key}</div>
                <div
                  className={`mt-1 font-mono text-sm ${
                    enabled ? 'text-emerald-400' : 'text-zinc-600'
                  }`}
                >
                  {enabled ? '● enabled' : '○ pending'}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="w-16 text-zinc-500">{label}</dt>
      <dd className={ok ? 'text-emerald-400' : 'text-zinc-200'}>{value}</dd>
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  22. ADD apps/web/src/pages/Runs.tsx                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Runs.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type Run, type RunStatus, api } from '../lib/api';

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function Runs() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api
        .listRuns()
        .then((rs) => alive && setRuns(rs))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
          <p className="mt-1 text-sm text-zinc-500">All fine-tuning jobs, newest first.</p>
        </div>
        <Link
          to="/runs/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Run
        </Link>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {runs === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : runs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No runs yet.{' '}
          <Link to="/runs/new" className="text-emerald-400 hover:underline">
            Start your first run →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 text-left">#</th>
                <th className="px-4 py-2.5 text-left">Dataset</th>
                <th className="px-4 py-2.5 text-left">Model</th>
                <th className="px-4 py-2.5 text-left">Method</th>
                <th className="px-4 py-2.5 text-right">Iters</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-right">Train loss</th>
                <th className="px-4 py-2.5 text-right">Val loss</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {runs.map((r) => (
                <tr key={r.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/runs/${r.id}`} className="text-emerald-400 hover:underline">
                      {r.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{r.dataset}</td>
                  <td className="px-4 py-2.5 text-xs text-zinc-500">
                    {r.base_model.replace(/^mlx-community\//, '')}
                  </td>
                  <td className="px-4 py-2.5">{r.method}</td>
                  <td className="px-4 py-2.5 text-right">{r.iters}</td>
                  <td className={`px-4 py-2.5 ${STATUS_STYLES[r.status]}`}>● {r.status}</td>
                  <td className="px-4 py-2.5 text-right">
                    {r.final_train_loss !== null ? r.final_train_loss.toFixed(3) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {r.final_val_loss !== null ? r.final_val_loss.toFixed(3) : '—'}
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
# ║  23. ADD apps/web/src/pages/NewRun.tsx                               ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/NewRun.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

export default function NewRun() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<BaseModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('mlx-community/gemma-3n-E2B-it-bf16');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(200);
  const [batchSize, setBatchSize] = useState(4);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);

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
      const run = await api.createRun({
        dataset,
        base_model: baseModel,
        method,
        iters,
        batch_size: batchSize,
        learning_rate: learningRate,
        num_layers: numLayers,
      });
      navigate(`/runs/${run.id}`);
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
        <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Make sure <code className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make trainer</code> is
          running in another terminal so the job actually starts.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {datasets.length === 0 ? (
              <option value="">— none found; run `make seed-data` —</option>
            ) : (
              datasets.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}  ({d.train_count} train / {d.valid_count} valid)
                </option>
              ))
            )}
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
          <p className="mt-1 text-xs text-zinc-500">
            {models.find((m) => m.hf_id === baseModel)?.notes ?? ''}
          </p>
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
          <Field label="Iterations">
            <Number value={iters} onChange={setIters} min={10} max={5000} step={10} />
          </Field>
          <Field label="Batch size">
            <Number value={batchSize} onChange={setBatchSize} min={1} max={32} step={1} />
          </Field>
          <Field label="Learning rate">
            <Number value={learningRate} onChange={setLearningRate} step={1e-5} />
          </Field>
          <Field label="Num layers (LoRA)">
            <Number value={numLayers} onChange={setNumLayers} min={1} max={48} step={1} />
          </Field>
        </div>

        {submitError && (
          <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Starting…' : 'Start training'}
        </button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function Number({
  value,
  onChange,
  ...rest
}: {
  value: number;
  onChange: (n: number) => void;
} & React.InputHTMLAttributes<HTMLInputElement>) {
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
# ║  24. ADD apps/web/src/pages/RunDetail.tsx                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/RunDetail.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import LiveLossChart from '../components/ratchet/LiveLossChart';
import { useRunMetrics } from '../hooks/useRunMetrics';
import { type Run, type RunStatus, api } from '../lib/api';

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const runId = id ? parseInt(id, 10) : undefined;
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { metrics, status, error: streamError } = useRunMetrics(runId);

  useEffect(() => {
    if (runId === undefined) return;
    let alive = true;
    const tick = () => {
      api
        .getRun(runId)
        .then((r) => alive && setRun(r))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [runId]);

  if (error) return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!run) return <div className="text-sm text-zinc-500">Loading run #{id}…</div>;

  const effectiveStatus = status ?? run.status;
  const latestTrain = [...metrics].reverse().find((m) => m.name === 'train_loss')?.value;
  const latestVal = [...metrics].reverse().find((m) => m.name === 'val_loss')?.value;
  const latestTps = [...metrics].reverse().find((m) => m.name === 'tokens_per_sec')?.value;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">Run #{run.id}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {run.dataset} · {run.base_model.replace(/^mlx-community\//, '')} · {run.method}
          </p>
        </div>
        <div className={`font-mono text-sm ${STATUS_STYLES[effectiveStatus]}`}>● {effectiveStatus}</div>
      </div>

      {run.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
          {run.error_message}
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="train loss" value={latestTrain?.toFixed(4) ?? '—'} />
        <Stat label="val loss" value={latestVal?.toFixed(4) ?? '—'} />
        <Stat label="tokens/sec" value={latestTps?.toFixed(0) ?? '—'} />
        <Stat label="iters" value={`${countSteps(metrics)} / ${run.iters}`} />
      </section>

      <LiveLossChart metrics={metrics} />

      {streamError && <div className="font-mono text-xs text-zinc-600">stream: {streamError}</div>}

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
          Configuration
        </h3>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={run.base_model} />
          <Row label="method" value={run.method} />
          <Row label="iters" value={String(run.iters)} />
          <Row label="batch_size" value={String(run.batch_size)} />
          <Row label="learning_rate" value={run.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(run.num_layers)} />
          <Row label="max_seq_length" value={String(run.max_seq_length)} />
          <Row label="grad_checkpoint" value={String(run.grad_checkpoint)} />
          <Row label="seed" value={String(run.seed)} />
        </dl>
      </section>
    </div>
  );
}

function countSteps(metrics: { step: number; name: string }[]): number {
  const steps = new Set<number>();
  for (const m of metrics) if (m.name === 'train_loss') steps.add(m.step);
  return steps.size > 0 ? Math.max(...steps) : 0;
}

function Stat({ label, value }: { label: string; value: string }) {
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

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  25. ADD apps/web/src/pages/Datasets.tsx                             ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Datasets.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { type DatasetInfo, api } from '../lib/api';

export default function Datasets() {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listDatasets()
      .then(setDatasets)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Available training datasets under <code className="text-zinc-400">data/datasets/</code>.
        </p>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {datasets === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : datasets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No datasets yet. Run{' '}
          <code className="rounded bg-zinc-800 px-1.5 py-0.5">make seed-data</code> to seed sample data.
        </div>
      ) : (
        <ul className="space-y-3">
          {datasets.map((d) => (
            <li key={d.name} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
              <div className="flex items-baseline justify-between gap-4">
                <h3 className="font-mono text-sm font-semibold text-zinc-100">{d.name}</h3>
                <div className="font-mono text-xs text-zinc-500">
                  {d.train_count} train · {d.valid_count} valid
                  {d.has_canary && ' · canary ✓'}
                </div>
              </div>
              {d.description && <p className="mt-1.5 text-sm text-zinc-400">{d.description}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  26. UPDATE README.md (Phase 1 quickstart)                           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > README.md <<'EOF'
# SLM-Forge

> Local-first SLM fine-tuning lab on Apple Silicon. **Phase 1: live training works.**

**Target:** MacBook Pro M3 Max, 36 GB · Python 3.12+ · React 19 · MLX-LM 0.30+

---

## Phase 1 quickstart

```bash
# One-time setup
./init-repo.sh                  # push to GitHub
make setup                      # uv + Node deps
make install-hermes             # Ollama + qwen2.5-coder:14b (Phase 2 prep)
make seed-data                  # copy sample datasets into data/datasets/
make download-base-model        # ~1.5 GB Gemma 3n E2B from HF

# Daily loop — two terminals
make dev                        # Terminal 1: UI + API in Docker
make trainer                    # Terminal 2: host trainer worker (Metal access)
```

Then open http://localhost:5173/runs/new, pick `stock-analyst` + default settings, click **Start training**, and watch the loss curve drop live.

---

## What's in Phase 1

- ✅ Sample dataset: `stock-analyst` (24 examples — smoke test scale)
- ✅ Host trainer worker: invokes `mlx_lm.lora`, streams stdout, posts metrics to API
- ✅ SQLite (via SQLModel) for runs + metrics
- ✅ REST API: `POST/GET /api/v1/runs`, `GET /api/v1/runs/{id}/metrics`
- ✅ SSE endpoint: `GET /api/v1/runs/{id}/stream` for live updates
- ✅ UI pages: Dashboard, Runs (list), New Run (form), Run Detail (live chart), Datasets
- ✅ Recharts-based live loss curve (train + val)
- ✅ Configurable: model, method (LoRA/DoRA/full SFT), iters, batch size, LR, num layers

## What's NOT in Phase 1 (next phases)

- ❌ Autoresearch ratchet loop (Phase 2)
- ❌ Hermes-driven hyperparameter mutation (Phase 2)
- ❌ Canary drift detection (Phase 2)
- ❌ Web/URL/S3 ingestion (Phase 3)
- ❌ GGUF export pipeline (Phase 4)
- ❌ Remaining 5 sample datasets (Phase 5)

---

## Why two terminals?

| Process | Where | Why |
|---|---|---|
| UI + API | Docker (`make dev`) | Lightweight, isolated |
| Trainer worker | Host (`make trainer`) | MLX needs Apple Metal/MPS — not available inside Linux containers |

This is a deliberate architectural choice — see `docs/ARCHITECTURE.md`.

---

## Project structure

```
.
├── apps/
│   ├── api/                # FastAPI: runs/metrics/datasets/models endpoints
│   └── web/                # React 19 + Vite + Tailwind + Recharts
├── packages/
│   ├── trainer/            # MLX-LM trainer worker (host process)
│   ├── ratchet/            # Autoresearch (Phase 2)
│   ├── ingest/             # Data ingestion (Phase 3)
│   └── exporter/           # GGUF export (Phase 4)
├── data/datasets/          # Datasets in mlx_lm.lora format
│   └── stock-analyst/      # train.jsonl + valid.jsonl + canary.jsonl
├── runs/                   # Per-run output (adapter/, config.yaml, training.log)
└── scripts/                # setup, seeding, model download
```
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  27. UPDATE docs/SETUP.md (Phase 1 notes)                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > docs/SETUP.md <<'EOF'
# Setup — Phase 1

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **uv** | Fast Python dep manager | `brew install uv` |
| **Node 22+** | React build | `brew install node` |
| **Docker Desktop** | UI + API containers | https://www.docker.com/products/docker-desktop |
| **Homebrew** | macOS package manager | https://brew.sh |
| **Python 3.12+** | uv will install if missing | (auto) |

## First run (one-time)

```bash
git clone git@github.com:amitsahuwin/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration

make setup                # creates uv.lock + package-lock.json, installs all deps
make install-hermes       # Ollama + qwen2.5-coder:14b (used in Phase 2)
make seed-data            # creates dataset files in data/datasets/
make download-base-model  # ~1.5 GB Gemma 3n E2B → ~/.cache/huggingface
```

## Daily loop — two terminals required

```bash
# Terminal 1: UI + API (Docker, with live reload)
make dev

# Terminal 2: host trainer worker (needs Metal/MPS access)
make trainer
```

Open http://localhost:5173.

## Running your first training

1. UI → click "+ New Run" (top-right) or visit `/runs/new`
2. Pick `stock-analyst` dataset
3. Defaults are fine for a smoke test (200 iters, LoRA, Gemma 3n E2B)
4. Click "Start training"
5. You're redirected to `/runs/<id>` with live loss curve

The trainer terminal shows live `mlx_lm.lora` output. The first run takes a few minutes to load the model into memory; subsequent runs are fast.

## Switching base model

The model dropdown shows the catalogue defined in `apps/api/routers/models.py`. To add a new model, edit `CATALOG` in that file (no UI rebuild needed — just refresh).

## Hermes provider switch

Default is local Ollama. To switch to Groq's free tier:

```bash
export GROQ_API_KEY=gsk_...
hermes config set provider groq
hermes config set model qwen-2.5-coder-32b
hermes config set api_key $GROQ_API_KEY
```

## Troubleshooting

- **`make trainer` says "mlx_lm.lora not found"** → `uv sync --extra trainer`
- **Trainer fails with "model not found"** → `make download-base-model` first
- **Training is very slow** → make sure you're NOT running the trainer in Docker; it must be on host. Check `ps aux | grep mlx_lm` and confirm it's running on your Mac, not inside a container.
- **First training step takes 60+ seconds** → normal, that's model load. Subsequent steps are fast.
- **Port 8000 already in use** → `lsof -ti:8000 | xargs kill`
- **Port 5173 already in use** → `lsof -ti:5173 | xargs kill`
- **Docker "Cannot connect"** → start Docker Desktop
- **SSE stream stops mid-training** → browsers throttle background tabs; keep the page focused, or refresh to resume
- **`make seed-data` says missing files** → the patch should have populated `data/datasets/stock-analyst/`. Verify with `ls data/datasets/stock-analyst/`
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  28. Done                                                            ║
# ╚══════════════════════════════════════════════════════════════════════╝

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 1 patch applied                                             ║
╚══════════════════════════════════════════════════════════════════════╝

What's new:
  • API:     runs, datasets, models endpoints + SSE stream
  • Trainer: packages/trainer (host worker, invokes mlx_lm.lora)
  • UI:      Dashboard, Runs list, New Run form, Run Detail with live loss chart
  • Data:    data/datasets/stock-analyst/ (24 examples, smoke-test scale)

Next steps:

  make setup                    # picks up new Python + Node deps
  make seed-data                # verify dataset files
  make download-base-model      # ~1.5 GB, one-time

  # Two terminals:
  make dev                      # T1: UI + API in Docker
  make trainer                  # T2: host trainer worker

Then visit http://localhost:5173/runs/new and start your first run.

If anything errors, paste it back and I'll fix before Phase 2.
MSG
