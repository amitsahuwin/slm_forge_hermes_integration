#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 4 patch (export pipeline)                         ║
# ║                                                                      ║
# ║  Adds:                                                               ║
# ║    • packages/exporter/   fuse → convert → quantize                  ║
# ║    • Export worker        (host process; reads queued exports)       ║
# ║    • /api/v1/exports      REST endpoints + SSE progress stream       ║
# ║    • Exports UI page      table + per-export detail                  ║
# ║    • Auto-export hook     completed sessions auto-export the winner  ║
# ║    • llama.cpp guard      friendly error if not installed via brew   ║
# ║    • iPhone deploy doc    docs/IPHONE_DEPLOY.md                      ║
# ║                                                                      ║
# ║  Apply AFTER Phase 3 is verified:                                    ║
# ║    chmod +x bootstrap_phase4.sh                                      ║
# ║    ./bootstrap_phase4.sh                                             ║
# ║    brew install llama.cpp     # if not already                       ║
# ║    make rebuild                                                      ║
# ║    make dev                                                          ║
# ║    make exporter              # NEW host worker                      ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Applying Phase 4 patch..."

mkdir -p packages/exporter
mkdir -p apps/api/models
mkdir -p apps/web/src/pages
mkdir -p exports

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  1. apps/api/models/export.py — Export table                         ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/models/export.py <<'EOF'
"""Export = one LoRA adapter being turned into GGUF artifacts for iPhone."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ExportStatus(str, Enum):
    QUEUED = "queued"
    FUSING = "fusing"
    CONVERTING = "converting"
    QUANTIZING = "quantizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QuantLevel(str, Enum):
    Q4_K_M = "Q4_K_M"
    Q5_K_M = "Q5_K_M"
    Q8_0 = "Q8_0"
    F16 = "F16"


def _now() -> datetime:
    return datetime.now(UTC)


class Export(SQLModel, table=True):
    __tablename__ = "exports"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)

    # Hyperparams snapshotted at export time (so re-running them is reproducible)
    base_model: str
    method: str

    # User-selected quant levels for this export job (comma-separated)
    # default: "Q4_K_M,Q8_0" — Q4 for iPhone, Q8 for reference
    quant_levels: str = "Q4_K_M,Q8_0"

    status: ExportStatus = ExportStatus.QUEUED
    error_message: str | None = None
    progress_text: str | None = None  # human-readable current step

    # Filesystem outputs (filled in as stages complete)
    fused_path: str | None = None
    gguf_f16_path: str | None = None
    gguf_q4_path: str | None = None
    gguf_q5_path: str | None = None
    gguf_q8_path: str | None = None

    # Final sizes (bytes) for the UI to display
    gguf_f16_bytes: int | None = None
    gguf_q4_bytes: int | None = None
    gguf_q5_bytes: int | None = None
    gguf_q8_bytes: int | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  2. Migrations for the exports table                                 ║
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

# Phase 2 migrations for runs table
_RUN_MIGRATIONS: list[tuple[str, str]] = [
    ("session_id", "INTEGER"),
    ("parent_run_id", "INTEGER"),
    ("iteration_number", "INTEGER"),
    ("was_accepted", "INTEGER"),
    ("mutation_reasoning", "TEXT"),
    ("canary_loss", "REAL"),
]

# Phase 4 — exports table is created by SQLModel; no ALTER needed unless schema changes


def _migrate_runs() -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)"))}
        for col, sql_type in _RUN_MIGRATIONS:
            if col not in existing:
                log.info("Migrating: ALTER TABLE runs ADD COLUMN %s %s", col, sql_type)
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {col} {sql_type}"))
                conn.commit()


def init_db() -> None:
    from apps.api.models import export as _export  # noqa: F401
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
# ║  3. apps/api/routers/exports.py                                      ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/exports.py <<'EOF'
"""Exports API — turn a completed run's adapter into iPhone-ready GGUF."""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.export import Export, ExportStatus, QuantLevel
from apps.api.models.run import Run
from apps.api.services.db import get_session

router = APIRouter()


class ExportCreate(BaseModel):
    run_id: int
    quant_levels: list[QuantLevel] = [QuantLevel.Q4_K_M, QuantLevel.Q8_0]


class ExportPatch(BaseModel):
    status: ExportStatus | None = None
    error_message: str | None = None
    progress_text: str | None = None
    fused_path: str | None = None
    gguf_f16_path: str | None = None
    gguf_q4_path: str | None = None
    gguf_q5_path: str | None = None
    gguf_q8_path: str | None = None
    gguf_f16_bytes: int | None = None
    gguf_q4_bytes: int | None = None
    gguf_q5_bytes: int | None = None
    gguf_q8_bytes: int | None = None


SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=Export)
def create_export(payload: ExportCreate, db: SessionDep) -> Export:
    run = db.get(Run, payload.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status.value != "completed":
        raise HTTPException(
            400,
            f"Run #{run.id} status is '{run.status.value}' — can only export completed runs",
        )
    if not run.adapter_path:
        raise HTTPException(400, f"Run #{run.id} has no adapter_path (nothing to fuse)")

    quants = ",".join(q.value for q in payload.quant_levels)
    export = Export(
        run_id=payload.run_id,
        base_model=run.base_model,
        method=run.method.value,
        quant_levels=quants,
    )
    db.add(export)
    db.commit()
    db.refresh(export)
    return export


@router.get("", response_model=list[Export])
def list_exports(
    db: SessionDep,
    status: ExportStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[Export]:
    stmt = select(Export).order_by(desc(Export.created_at)).limit(limit)
    if status is not None:
        stmt = (
            select(Export).where(Export.status == status)
            .order_by(desc(Export.created_at)).limit(limit)
        )
    return list(db.exec(stmt).all())


@router.get("/{xid}", response_model=Export)
def get_export(xid: int, db: SessionDep) -> Export:
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")
    return e


@router.patch("/{xid}", response_model=Export)
def patch_export(xid: int, payload: ExportPatch, db: SessionDep) -> Export:
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(e, k, v)

    now = datetime.now(UTC)
    if payload.status == ExportStatus.FUSING and e.started_at is None:
        e.started_at = now
    if payload.status in {ExportStatus.COMPLETED, ExportStatus.FAILED, ExportStatus.CANCELLED}:
        e.completed_at = now

    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.get("/{xid}/stream")
async def stream_export(xid: int) -> EventSourceResponse:
    async def event_gen() -> AsyncGenerator[dict[str, str], None]:
        last_status: str | None = None
        last_progress: str | None = None
        terminal = {
            ExportStatus.COMPLETED.value, ExportStatus.FAILED.value, ExportStatus.CANCELLED.value,
        }
        from apps.api.services.db import engine
        from sqlmodel import Session as _Session

        while True:
            with _Session(engine) as s:
                e = s.get(Export, xid)
                if not e:
                    yield {"event": "error", "data": json.dumps({"message": "Export not found"})}
                    return

                if e.status.value != last_status or e.progress_text != last_progress:
                    last_status = e.status.value
                    last_progress = e.progress_text
                    yield {
                        "event": "update",
                        "data": json.dumps({
                            "status": e.status.value,
                            "progress_text": e.progress_text,
                            "gguf_q4_path": e.gguf_q4_path,
                            "gguf_q4_bytes": e.gguf_q4_bytes,
                        }),
                    }

                if e.status.value in terminal:
                    yield {"event": "done", "data": json.dumps({"status": e.status.value})}
                    return

            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())


@router.get("/{xid}/download/{variant}")
def download_export(xid: int, variant: str, db: SessionDep) -> FileResponse:
    """Download a specific GGUF variant file. variant in {f16, q4, q5, q8}."""
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")

    path_map = {
        "f16": e.gguf_f16_path,
        "q4": e.gguf_q4_path,
        "q5": e.gguf_q5_path,
        "q8": e.gguf_q8_path,
    }
    target = path_map.get(variant)
    if not target:
        raise HTTPException(404, f"Variant '{variant}' not available for this export")

    # The path in the DB is host-side; inside Docker it's mounted at /app/exports/...
    # Try both — if exports dir is mounted into the container, the host path works
    # because we use the same project-relative layout.
    candidates = [target, target.replace("/Users/", "/app/", 1) if "/Users/" in target else target]
    for p in candidates:
        if p and os.path.exists(p):
            return FileResponse(p, filename=os.path.basename(p), media_type="application/octet-stream")

    raise HTTPException(404, f"File not found on disk: {target}")
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  4. UPDATE apps/api/main.py — mount exports router                   ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/main.py <<'EOF'
"""SLM-Forge API — Phase 4."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import datasets, exports, ingest, models, runs, sessions
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


app = FastAPI(title="SLM-Forge API", version="0.5.0", lifespan=lifespan)

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
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(exports.router, prefix="/api/v1/exports", tags=["exports"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "SLM-Forge API", "version": "0.5.0", "docs": "/docs"}


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys
    return HealthResponse(
        status="ok",
        version="0.5.0",
        phase="Phase 4 — export to GGUF",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": True,
            "autoresearch": True,
            "ingestion": True,
            "export_gguf": True,
            "hermes_bridge": True,
        },
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  5. UPDATE docker-compose.yml — mount exports/ into API              ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > docker-compose.yml <<'EOF'
services:
  api:
    build:
      context: .
      dockerfile: apps/api/Dockerfile
    container_name: slm-forge-api
    ports:
      - "8000:8000"
    volumes:
      - ./apps/api:/app/apps/api
      - ./packages:/app/packages
      - ./data:/app/data
      - ./runs:/app/runs:ro
      - ./exports:/app/exports:ro
    environment:
      - PYTHONDONTWRITEBYTECODE=1
      - PYTHONUNBUFFERED=1
      - SLM_FORGE_DB_URL=sqlite:////app/data/slm_forge.db
    restart: unless-stopped

  web:
    build:
      context: ./apps/web
      dockerfile: Dockerfile
    container_name: slm-forge-web
    ports:
      - "5173:5173"
    volumes:
      - ./apps/web/src:/app/src
      - ./apps/web/public:/app/public
      - ./apps/web/index.html:/app/index.html
      - ./apps/web/tailwind.config.ts:/app/tailwind.config.ts
      - ./apps/web/postcss.config.js:/app/postcss.config.js
      - ./apps/web/vite.config.ts:/app/vite.config.ts
      - ./apps/web/tsconfig.json:/app/tsconfig.json
    environment:
      - VITE_API_URL=http://localhost:8000
    depends_on:
      - api
    restart: unless-stopped
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  6. packages/exporter/pipeline.py — fuse, convert, quantize          ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/exporter/__init__.py <<'EOF'
EOF

cat > packages/exporter/pipeline.py <<'EOF'
"""Runs one export job: LoRA adapter → fused HF → GGUF F16 → quantized variants."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("exporter.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"
EXPORTS_ROOT = PROJECT_ROOT / "exports"

QUANT_FILENAME = {
    "F16": "model-F16.gguf",
    "Q4_K_M": "model-Q4_K_M.gguf",
    "Q5_K_M": "model-Q5_K_M.gguf",
    "Q8_0": "model-Q8_0.gguf",
}

# DB field name → ExportPatch field name for storing each variant's path & size
DB_FIELD_PATH = {
    "F16": "gguf_f16_path",
    "Q4_K_M": "gguf_q4_path",
    "Q5_K_M": "gguf_q5_path",
    "Q8_0": "gguf_q8_path",
}
DB_FIELD_BYTES = {
    "F16": "gguf_f16_bytes",
    "Q4_K_M": "gguf_q4_bytes",
    "Q5_K_M": "gguf_q5_bytes",
    "Q8_0": "gguf_q8_bytes",
}


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/exports/{xid}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /exports/%s failed: %s", xid, e)


def _find_llama_quantize() -> str | None:
    """Locate the llama-quantize binary (Homebrew or PATH)."""
    candidates = [
        "llama-quantize",
        "/opt/homebrew/bin/llama-quantize",
        "/usr/local/bin/llama-quantize",
    ]
    for c in candidates:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _find_convert_script() -> str | None:
    """Locate llama.cpp's convert_hf_to_gguf.py.

    Homebrew installs it under /opt/homebrew/share/llama.cpp/ on Apple Silicon
    or /usr/local/share/llama.cpp/ on Intel. Also check standard locations.
    """
    candidates = [
        "/opt/homebrew/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/Cellar/llama.cpp/*/share/llama.cpp/convert_hf_to_gguf.py",
        "/usr/local/share/llama.cpp/convert_hf_to_gguf.py",
        # Some newer Homebrew installs use a libexec path
        "/opt/homebrew/libexec/llama.cpp/convert_hf_to_gguf.py",
    ]
    import glob
    for pattern in candidates:
        for path in glob.glob(pattern):
            if os.path.exists(path):
                return path
    # Last resort: try `brew --prefix llama.cpp` to find it
    try:
        r = subprocess.run(
            ["brew", "--prefix", "llama.cpp"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            prefix = r.stdout.strip()
            for sub in ("share/llama.cpp", "libexec"):
                p = Path(prefix) / sub / "convert_hf_to_gguf.py"
                if p.exists():
                    return str(p)
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_tools() -> tuple[str, str]:
    """Verify llama.cpp is installed. Returns (quantize_path, convert_script_path)."""
    q = _find_llama_quantize()
    if not q:
        raise RuntimeError(
            "llama-quantize not found. Install: brew install llama.cpp"
        )
    c = _find_convert_script()
    if not c:
        raise RuntimeError(
            "convert_hf_to_gguf.py not found. "
            "Verify with: brew list llama.cpp | grep convert_hf_to_gguf.py"
        )
    return q, c


def _run_subprocess(cmd: list[str], log_path: Path, *, env: dict | None = None) -> int:
    """Run a subprocess, streaming output to both stdout and the log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(cmd)}\n\n")
        lf.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env or os.environ.copy(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"    {line.rstrip()}", flush=True)
            lf.write(line)
            lf.flush()
        proc.wait()
        return proc.returncode


def run_export_job(export_row: dict, api_url: str) -> None:
    """Run one export end-to-end."""
    xid = export_row["id"]
    run_id = export_row["run_id"]
    base_model = export_row["base_model"]
    quant_levels = [q.strip() for q in export_row["quant_levels"].split(",") if q.strip()]

    log.info("─── Export #%s for run #%s (quants=%s) ───", xid, run_id, quant_levels)

    # ── Sanity: tools ────────────────────────────────────────────
    try:
        quantize_bin, convert_script = _check_tools()
    except RuntimeError as e:
        log.error(str(e))
        _patch_export(api_url, xid, status="failed", error_message=str(e))
        return

    # ── Sanity: adapter exists ───────────────────────────────────
    adapter_dir = RUNS_ROOT / str(run_id) / "adapter"
    if not adapter_dir.exists():
        msg = f"Adapter dir not found: {adapter_dir}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    export_dir = EXPORTS_ROOT / str(xid)
    fused_dir = export_dir / "fused"
    gguf_dir = export_dir / "gguf"
    log_path = export_dir / "export.log"
    fused_dir.mkdir(parents=True, exist_ok=True)
    gguf_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: mlx_lm.fuse → safetensors ───────────────────────
    log.info("Stage 1/3: mlx_lm.fuse (LoRA adapter + base → merged safetensors)")
    _patch_export(api_url, xid, status="fusing", progress_text="Fusing LoRA into base model…")

    py = sys.executable
    fuse_cmd = [
        py, "-m", "mlx_lm", "fuse",
        "--model", base_model,
        "--adapter-path", str(adapter_dir),
        "--save-path", str(fused_dir),
    ]
    # Older mlx-lm versions used 'mlx_lm.fuse' as a direct module
    probe = subprocess.run(
        [py, "-m", "mlx_lm", "fuse", "--help"], capture_output=True, text=True, timeout=15
    )
    if probe.returncode != 0:
        fuse_cmd = [
            py, "-m", "mlx_lm.fuse",
            "--model", base_model,
            "--adapter-path", str(adapter_dir),
            "--save-path", str(fused_dir),
        ]

    env = os.environ.copy()
    scripts = sysconfig.get_path("scripts")
    if scripts:
        env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"

    rc = _run_subprocess(fuse_cmd, log_path, env=env)
    if rc != 0:
        msg = f"mlx_lm.fuse exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(api_url, xid, fused_path=str(fused_dir))

    # ── Stage 2: convert_hf_to_gguf.py → F16 GGUF ────────────────
    log.info("Stage 2/3: convert_hf_to_gguf.py (HF safetensors → F16 GGUF)")
    _patch_export(api_url, xid, status="converting", progress_text="Converting to F16 GGUF…")

    f16_path = gguf_dir / QUANT_FILENAME["F16"]
    convert_cmd = [
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]
    rc = _run_subprocess(convert_cmd, log_path, env=env)
    if rc != 0:
        msg = f"convert_hf_to_gguf.py exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    if not f16_path.exists():
        msg = f"Conversion succeeded but F16 GGUF not at expected path: {f16_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(
        api_url, xid,
        gguf_f16_path=str(f16_path),
        gguf_f16_bytes=f16_path.stat().st_size,
    )

    # ── Stage 3: llama-quantize → Q4_K_M / Q5_K_M / Q8_0 ─────────
    log.info("Stage 3/3: llama-quantize (F16 → user-selected quants)")
    _patch_export(api_url, xid, status="quantizing", progress_text="Quantizing variants…")

    for quant in quant_levels:
        if quant == "F16":
            continue  # already produced

        target = gguf_dir / QUANT_FILENAME[quant]
        log.info("  quantizing → %s", target.name)
        _patch_export(api_url, xid, progress_text=f"Quantizing {quant}…")

        rc = _run_subprocess(
            [quantize_bin, str(f16_path), str(target), quant],
            log_path,
            env=env,
        )
        if rc != 0:
            msg = f"llama-quantize {quant} exited with code {rc}. See {log_path}"
            log.error(msg)
            _patch_export(api_url, xid, status="failed", error_message=msg)
            return

        if target.exists():
            _patch_export(
                api_url, xid,
                **{DB_FIELD_PATH[quant]: str(target), DB_FIELD_BYTES[quant]: target.stat().st_size},
            )

    # ── Done ─────────────────────────────────────────────────────
    log.info("Export #%s completed.", xid)
    _patch_export(
        api_url, xid,
        status="completed",
        progress_text="Done. Send the .gguf to your iPhone (see docs/IPHONE_DEPLOY.md).",
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  7. packages/exporter/__main__.py — host worker entrypoint           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/exporter/__main__.py <<'EOF'
"""Export worker — polls /api/v1/exports for queued jobs and processes them.

Run via:
    uv run python -m packages.exporter

Like the trainer, this must run on host (not Docker) because mlx_lm.fuse
needs Apple Metal access.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

# Auto-load .env from project root
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from packages.exporter.pipeline import run_export_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("exporter.worker")

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(
            f"{API_URL}/api/v1/exports",
            params={"status": "queued", "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[-1] if rows else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Exporter worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

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

    # Pre-flight: verify llama.cpp tools BEFORE polling, so the user gets a
    # clean error rather than a queued export silently failing.
    from packages.exporter.pipeline import _check_tools
    try:
        q, c = _check_tools()
        log.info("llama-quantize: %s", q)
        log.info("convert_hf_to_gguf.py: %s", c)
    except RuntimeError as e:
        log.error("Pre-flight failed: %s", e)
        return 1

    log.info("Ready. Polling for queued exports every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    while True:
        try:
            export = fetch_next_queued()
            if export is None:
                time.sleep(POLL_INTERVAL)
                continue
            run_export_job(export, api_url=API_URL)
        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  8. UPDATE Makefile — add 'make exporter', 'make check-llamacpp'     ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > Makefile <<'EOF'
.PHONY: help setup install-hermes hermes-install-skills dev down build rebuild logs \
        trainer ratchet exporter check-llamacpp \
        seed-data download-base-model train-sample clean ensure-lock ensure-trainer-installed

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'

setup: ## Install all deps (Python via uv, Node via npm)
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	uv sync --all-extras
	cd apps/web && npm install
	@if uv run python -c "import mlx_lm" 2>/dev/null; then echo "✓ mlx-lm installed."; else echo "✗ mlx-lm did NOT install."; fi

install-hermes: ## Install Ollama + Hermes Agent + qwen3:30b-a3b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download the default base model from HF
	bash scripts/download_base_model.sh

ensure-trainer-installed:
	@if ! uv run python -c "import mlx_lm" 2>/dev/null; then \
		echo "✗ mlx-lm not installed. Run: uv sync --all-extras"; exit 1; \
	fi
	@if ! uv run python -m mlx_lm lora --help >/dev/null 2>&1; then \
		if ! uv run python -m mlx_lm.lora --help >/dev/null 2>&1; then \
			echo "✗ mlx-lm installed but module form fails. Run: uv sync --all-extras --refresh"; exit 1; \
		fi; \
	fi

check-llamacpp: ## Verify llama.cpp (llama-quantize + convert_hf_to_gguf.py) is installed
	@if ! command -v llama-quantize >/dev/null 2>&1 && ! [ -x /opt/homebrew/bin/llama-quantize ]; then \
		echo "✗ llama-quantize not found. Install: brew install llama.cpp"; exit 1; \
	fi
	@PREFIX=$$(brew --prefix llama.cpp 2>/dev/null); \
	if [ -z "$$PREFIX" ]; then \
		echo "✗ llama.cpp not installed via Homebrew. Install: brew install llama.cpp"; exit 1; \
	fi; \
	if ! find "$$PREFIX" -name convert_hf_to_gguf.py 2>/dev/null | grep -q .; then \
		echo "✗ convert_hf_to_gguf.py not found under $$PREFIX"; exit 1; \
	fi
	@echo "✓ llama.cpp tools detected"

trainer: ensure-trainer-installed ## Run the host trainer worker
	uv run python -m packages.trainer

ratchet: ## Run the autoresearch ratchet worker
	@if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then \
		echo "✗ Ollama not reachable at :11434"; exit 1; \
	fi
	uv run python -m packages.ratchet

exporter: ensure-trainer-installed check-llamacpp ## Run the GGUF export worker
	@echo "→ Starting exporter worker..."
	uv run python -m packages.exporter

ensure-lock:
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API
	docker compose up

rebuild: ensure-lock ## Force-rebuild Docker images
	docker compose down
	docker compose build --no-cache

down: ## Stop dev stack
	docker compose down

build: ensure-lock
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
# ║  9. Update lib/api.ts with exports endpoints                         ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat >> apps/web/src/lib/api.ts <<'EOF'

// ─── Phase 4 exports ──────────────────────────────────────────

export type ExportStatus =
  | 'queued' | 'fusing' | 'converting' | 'quantizing'
  | 'completed' | 'failed' | 'cancelled';

export type ExportRow = {
  id: number;
  run_id: number;
  base_model: string;
  method: string;
  quant_levels: string;
  status: ExportStatus;
  error_message: string | null;
  progress_text: string | null;
  fused_path: string | null;
  gguf_f16_path: string | null;
  gguf_q4_path: string | null;
  gguf_q5_path: string | null;
  gguf_q8_path: string | null;
  gguf_f16_bytes: number | null;
  gguf_q4_bytes: number | null;
  gguf_q5_bytes: number | null;
  gguf_q8_bytes: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export const exportsApi = {
  list: () => jget<ExportRow[]>('/api/v1/exports'),
  get: (id: number) => jget<ExportRow>(`/api/v1/exports/${id}`),
  create: (body: { run_id: number; quant_levels?: string[] }) =>
    jpost<ExportRow>('/api/v1/exports', body),
  downloadUrl: (id: number, variant: 'f16' | 'q4' | 'q5' | 'q8') =>
    `${API_URL}/api/v1/exports/${id}/download/${variant}`,
};
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  10. Update Nav.tsx — add "Exports" tab                              ║
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
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Dashboard</NavLink>
            <NavLink to="/sessions" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Sessions</NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Runs</NavLink>
            <NavLink to="/exports" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Exports</NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>Datasets</NavLink>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <NavLink to="/datasets/new" className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900">+ Dataset</NavLink>
          <NavLink to="/sessions/new" className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500">+ Session</NavLink>
        </div>
      </div>
    </header>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  11. Update App.tsx — add /exports route                             ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/App.tsx <<'EOF'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import Exports from './pages/Exports';
import NewDataset from './pages/NewDataset';
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
            <Route path="/exports" element={<Exports />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/new" element={<NewDataset />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  12. Exports.tsx — the new exports page                              ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Exports.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type ExportRow, type ExportStatus, exportsApi } from '../lib/api';

const STATUS_STYLES: Record<ExportStatus, string> = {
  queued: 'text-zinc-400',
  fusing: 'text-amber-400',
  converting: 'text-amber-400',
  quantizing: 'text-amber-400',
  completed: 'text-emerald-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

function humanBytes(n: number | null): string {
  if (n === null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let val = n;
  let u = 0;
  while (val >= 1024 && u < units.length - 1) {
    val /= 1024;
    u++;
  }
  return `${val.toFixed(val > 10 ? 0 : 1)} ${units[u]}`;
}

export default function Exports() {
  const [items, setItems] = useState<ExportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      exportsApi.list()
        .then((rs) => alive && setItems(rs))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(iv); };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Exports</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Fine-tuned models exported to GGUF for use on iPhone (PocketPal AI / Edge Gallery).
        </p>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {items === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No exports yet. Open a completed run from{' '}
          <Link to="/runs" className="text-emerald-400 hover:underline">Runs</Link>
          {' '}and click "Export to GGUF".
        </div>
      ) : (
        <ul className="space-y-3">
          {items.map((e) => (
            <li key={e.id} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
              <div className="flex items-baseline justify-between">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-sm text-zinc-100">Export #{e.id}</span>
                  <span className="text-xs text-zinc-500">
                    from <Link to={`/runs/${e.run_id}`} className="text-emerald-400 hover:underline">run #{e.run_id}</Link>
                  </span>
                  <span className="text-xs text-zinc-500">·</span>
                  <span className="font-mono text-xs text-zinc-500">{e.base_model.replace(/^mlx-community\//, '')}</span>
                </div>
                <span className={`font-mono text-xs ${STATUS_STYLES[e.status]}`}>● {e.status}</span>
              </div>

              {e.progress_text && (
                <p className="mt-2 font-mono text-xs text-zinc-400">{e.progress_text}</p>
              )}

              {e.error_message && (
                <p className="mt-2 font-mono text-xs text-rose-300">{e.error_message}</p>
              )}

              {e.status === 'completed' && (
                <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
                  <Variant label="Q4_K_M (iPhone)" path={e.gguf_q4_path} bytes={e.gguf_q4_bytes} href={exportsApi.downloadUrl(e.id, 'q4')} highlight />
                  <Variant label="Q5_K_M" path={e.gguf_q5_path} bytes={e.gguf_q5_bytes} href={exportsApi.downloadUrl(e.id, 'q5')} />
                  <Variant label="Q8_0" path={e.gguf_q8_path} bytes={e.gguf_q8_bytes} href={exportsApi.downloadUrl(e.id, 'q8')} />
                  <Variant label="F16 (reference)" path={e.gguf_f16_path} bytes={e.gguf_f16_bytes} href={exportsApi.downloadUrl(e.id, 'f16')} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 text-xs text-zinc-400">
        <strong className="text-zinc-300">iPhone deployment:</strong> download the <code className="font-mono">Q4_K_M.gguf</code> file,
        AirDrop it to your iPhone, then open PocketPal AI → "Add Local Model" → select the file.
        Full instructions in <code className="font-mono">docs/IPHONE_DEPLOY.md</code>.
      </div>
    </div>
  );
}

function Variant({
  label, path, bytes, href, highlight,
}: {
  label: string;
  path: string | null;
  bytes: number | null;
  href: string;
  highlight?: boolean;
}) {
  if (!path) {
    return (
      <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2">
        <div className="font-mono text-xs text-zinc-600">{label}</div>
        <div className="mt-0.5 font-mono text-xs text-zinc-700">not produced</div>
      </div>
    );
  }
  return (
    <a
      href={href}
      className={`rounded-md border px-3 py-2 transition-colors hover:bg-zinc-800/60 ${
        highlight ? 'border-emerald-700 bg-emerald-950/30' : 'border-zinc-800 bg-zinc-900/40'
      }`}
    >
      <div className="font-mono text-xs text-zinc-400">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-zinc-100">{humanBytes(bytes)} ↓</div>
    </a>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  13. RunDetail.tsx — add "Export to GGUF" button                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Inject the export button into the existing RunDetail.tsx
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/web/src/pages/RunDetail.tsx")
if not p.exists():
    print("⚠ apps/web/src/pages/RunDetail.tsx not found — skipping in-place edit")
    raise SystemExit(0)

text = p.read_text()
if "exportsApi" in text:
    print("  ✓ RunDetail already has export button")
    raise SystemExit(0)

# Add import
text = text.replace(
    "import { type Run, type RunStatus, api } from '../lib/api';",
    "import { type Run, type RunStatus, api, exportsApi } from '../lib/api';",
    1,
)

# Inject an Export button into the header section
hdr_needle = "<div className={`font-mono text-sm ${STATUS_STYLES[effectiveStatus]}`}>● {effectiveStatus}</div>"
hdr_replacement = """<div className="flex items-center gap-3">
          {run.status === 'completed' && run.adapter_path && (
            <button
              onClick={async () => {
                try {
                  const x = await exportsApi.create({ run_id: run.id, quant_levels: ['Q4_K_M', 'Q8_0'] });
                  window.location.href = `/exports`;
                  console.log('Queued export', x.id);
                } catch (e) {
                  alert(`Failed to queue export: ${e instanceof Error ? e.message : String(e)}`);
                }
              }}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
            >
              Export to GGUF →
            </button>
          )}
          <div className={`font-mono text-sm ${STATUS_STYLES[effectiveStatus]}`}>● {effectiveStatus}</div>
        </div>"""

if hdr_needle in text:
    text = text.replace(hdr_needle, hdr_replacement, 1)
    p.write_text(text)
    print("  ✓ Added Export to GGUF button to RunDetail")
else:
    print("  ⚠ Could not find header insertion point in RunDetail.tsx — manually add the export button")
PYEOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  14. docs/IPHONE_DEPLOY.md                                           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > docs/IPHONE_DEPLOY.md <<'EOF'
# iPhone deployment

Run your fine-tuned model on iPhone offline via PocketPal AI or Google Edge Gallery.

## Prerequisites

- PocketPal AI installed on iPhone (free on the App Store)
- An exported `.gguf` file from SLM-Forge (e.g. `model-Q4_K_M.gguf`)

## Recommended quantization

| Variant | Size (3B model) | Quality | Use when |
|---|---|---|---|
| **Q4_K_M** | ~1.9 GB | Good | **Default for iPhone** |
| Q5_K_M | ~2.3 GB | Better | Newer iPhones with plenty of storage |
| Q8_0 | ~3.2 GB | Near-F16 | Reference/comparison |
| F16 | ~6 GB | Full | Desktop / debugging |

## Transfer methods

### Option A — AirDrop (easiest)

1. In SLM-Forge UI, navigate to **Exports**
2. Click the `Q4_K_M (iPhone)` tile to download the file to your Mac
3. Right-click the downloaded `.gguf` → Share → AirDrop → your iPhone
4. On iPhone, accept and save to Files

### Option B — Upload to a private HuggingFace repo

If file size or AirDrop is annoying:

```bash
# On your Mac (one-time setup)
huggingface-cli login
huggingface-cli repo create my-finetuned-models --private --repo-type model

# Upload
huggingface-cli upload my-finetuned-models ./exports/<id>/gguf/model-Q4_K_M.gguf
```

Then in PocketPal AI: search `<your-username>/my-finetuned-models` → download.

### Option C — USB / Files app

1. Open Finder, connect iPhone
2. Drag the `.gguf` into the iPhone's Files area
3. In PocketPal, browse to that location

## Loading the model in PocketPal AI

1. Open PocketPal AI
2. Tap **"Add Local Model"** (or the `+` icon)
3. Browse Files → locate your `.gguf`
4. Tap to load (takes 5–15s for a 3B Q4_K_M)
5. Start chatting

## Tuning PocketPal's inference settings

For Qwen-based models, set:
- **Context length:** 4096 (or 8192 if your iPhone has 8GB+ RAM)
- **Chat template:** `Qwen2` (PocketPal auto-detects most of the time)
- **Temperature:** 0.4–0.7 for analysis tasks, 0.7–1.0 for creative

If responses look garbled, the chat template is probably wrong — manually
set it in PocketPal's per-model settings.

## Expected iPhone performance

On iPhone 15 Pro / 16 Pro (8GB RAM):
- Qwen2.5-3B Q4_K_M: ~12–18 tokens/sec
- Llama 3.2 3B Q4_K_M: ~10–15 tokens/sec

On iPhone 13 / 14 (4-6GB RAM):
- Same models: ~5–10 tokens/sec, may swap heavily

If the device gets warm or slow, try a smaller base model (1B-class) or
more aggressive quant (Q3_K_M — not produced by default, ask for it manually).
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  15. Auto-export hook for completed sessions                         ║
# ╚══════════════════════════════════════════════════════════════════════╝
# This adds a small block to packages/ratchet/loop.py to auto-queue an export
# for the session's best run at the end. We do this via a small patch script.
python3 - <<'PYEOF'
from pathlib import Path
p = Path("packages/ratchet/loop.py")
if not p.exists():
    print("⚠ packages/ratchet/loop.py not found — skipping auto-export hook")
    raise SystemExit(0)

text = p.read_text()
if "auto-queue export" in text:
    print("  ✓ Auto-export hook already present")
    raise SystemExit(0)

needle = 'api.patch_session(session_id, status="completed")'
replacement = '''api.patch_session(session_id, status="completed")

    # Auto-queue export for the session's winner (if any)
    if best_run_id is not None:
        try:
            httpx.post(
                f"{api.base}/api/v1/exports",
                json={"run_id": best_run_id, "quant_levels": ["Q4_K_M", "Q8_0"]},
                timeout=10,
            ).raise_for_status()
            log.info("  auto-queue export for best run #%s", best_run_id)
        except Exception as e:  # noqa: BLE001
            log.warning("  failed to auto-queue export: %s", e)'''

if needle in text:
    text = text.replace(needle, replacement, 1)
    p.write_text(text)
    print("  ✓ Added auto-export hook to ratchet loop")
else:
    print("  ⚠ Could not find session-complete line in loop.py — auto-export disabled")
PYEOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  16. exports/.gitkeep + .gitignore for export artifacts              ║
# ╚══════════════════════════════════════════════════════════════════════╝
touch exports/.gitkeep
if ! grep -q "exports/\*/" .gitignore 2>/dev/null; then
    cat >> .gitignore <<'EOF'

# Phase 4 export artifacts (large files; never commit)
exports/*/
!exports/.gitkeep
EOF
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 4 patch applied                                             ║
╚══════════════════════════════════════════════════════════════════════╝

What's new:
  • API:          /api/v1/exports endpoints (create, list, get, patch, stream, download)
  • Schema:       new exports table (auto-migrated on next API start)
  • Exporter:     packages/exporter/ (host worker — needs Metal access)
  • UI pages:     /exports (list + download links)
  • UI button:    "Export to GGUF →" on completed run pages
  • Auto-export:  session winners auto-queue when session completes
  • Docs:         docs/IPHONE_DEPLOY.md

Before running:

  brew install llama.cpp    # ⚠ REQUIRED for fuse/convert/quantize

Then:

  make rebuild              # picks up new API router + new docker mount
  make dev                  # T1
  make trainer              # T2 (still running)
  make ratchet              # T3 (still running)
  make exporter             # T4 — NEW worker

Test:
  1. Open /runs and pick any completed run
  2. Click "Export to GGUF →"
  3. You'll be redirected to /exports
  4. Watch progress: fusing → converting → quantizing → completed
  5. Click the Q4_K_M tile to download the .gguf
  6. AirDrop to iPhone → load in PocketPal AI

Disk usage warning: each export = ~10-20 GB of intermediate files.
Manually delete exports/<id>/ subdirs when done.
MSG
