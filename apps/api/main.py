"""SLM-Forge API."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import (
    admin,
    chat,
    datasets,
    datasets_detail,
    exports,
    hermes,
    ingest,
    ingest_v2,
    logs,
    models,
    runs,
    sessions,
    synth,
)
from apps.api.services.db import init_db
from packages._logging import setup_worker_logging

API_VERSION = "0.6.0"
_started_at = time.monotonic()
_started_iso = datetime.now(UTC).isoformat()


class HealthResponse(BaseModel):
    status: str
    version: str
    python: str
    started_at: str
    uptime_seconds: int
    capabilities: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    setup_worker_logging("api")
    init_db()
    yield


app = FastAPI(title="SLM-Forge API", version="0.6.0", lifespan=lifespan)

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
app.include_router(
    datasets_detail.router, prefix="/api/v1/datasets", tags=["datasets"]
)
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(exports.router, prefix="/api/v1/exports", tags=["exports"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
# Phase 6 — live logs, Hermes status, LangGraph chat
app.include_router(logs.router, prefix="/api/v1", tags=["logs"])
app.include_router(hermes.router, prefix="/api/v1/hermes", tags=["hermes"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
# Phase G — Ollama-driven dataset synthesis
app.include_router(synth.router, prefix="/api/v1/synth", tags=["synth"])
# Phase H — Universal ingest converter (uploads any format → standard splits)
app.include_router(ingest_v2.router, prefix="/api/v1/ingest", tags=["ingest"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "SLM-Forge API", "version": API_VERSION, "docs": "/docs"}


def _capabilities() -> dict[str, bool]:
    """Probe what's actually available at runtime, not hardcoded.

    The trainer and exporter run on the **host** (MLX is Apple-Silicon-only,
    llama.cpp lives outside the container) so we can't detect them via
    in-process imports inside the API. Instead we read each worker's
    persisted heartbeat from SQLite — if the worker is heartbeating, the
    capability is live.
    """
    from sqlmodel import Session

    from apps.api.models.heartbeat import WorkerHeartbeat
    from apps.api.routers.hermes import WORKER_STALE_AFTER, _aware
    from apps.api.services.db import engine

    caps: dict[str, bool] = {}
    now = datetime.now(UTC)

    # Heartbeat-driven capabilities — trainer + exporter + ratchet run on host.
    try:
        with Session(engine) as db:
            for worker_name, cap_name in (
                ("trainer", "trainer"),
                ("exporter", "export_gguf"),
                ("ratchet", "autoresearch"),
            ):
                hb = db.get(WorkerHeartbeat, worker_name)
                caps[cap_name] = bool(
                    hb is not None
                    and (now - _aware(hb.last_seen)) < WORKER_STALE_AFTER
                )
    except Exception:  # noqa: BLE001
        # Fall back to all-false on DB hiccup rather than crashing /health.
        caps.setdefault("trainer", False)
        caps.setdefault("export_gguf", False)
        caps.setdefault("autoresearch", False)

    # Ingestion deps run inside the API container, so this in-process import is correct.
    try:
        __import__("trafilatura")
        caps["ingestion"] = True
    except ImportError:
        caps["ingestion"] = False

    # Hermes bridge — verify Ollama is reachable from the API container.
    try:
        from packages.ratchet.hermes_bridge import OLLAMA_URL

        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=2)
        caps["hermes_bridge"] = r.status_code == 200
    except Exception:  # noqa: BLE001
        caps["hermes_bridge"] = False

    # Chat LLM is the same probe — labeled separately for clarity.
    caps["chat_llm"] = caps["hermes_bridge"]

    # Autoresearch requires BOTH the ratchet worker AND the bridge to be live.
    caps["autoresearch"] = caps.get("autoresearch", False) and caps["hermes_bridge"]

    return caps


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys

    return HealthResponse(
        status="ok",
        version=API_VERSION,
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        started_at=_started_iso,
        uptime_seconds=int(time.monotonic() - _started_at),
        capabilities=_capabilities(),
    )
