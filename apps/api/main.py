"""SLM-Forge API — Phase 4."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import (
    admin,
    chat,
    datasets,
    exports,
    hermes,
    ingest,
    logs,
    models,
    runs,
    sessions,
)
from apps.api.services.db import init_db
from packages._logging import setup_worker_logging


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    python: str
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
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])
app.include_router(exports.router, prefix="/api/v1/exports", tags=["exports"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
# Phase 6 — live logs, Hermes status, LangGraph chat
app.include_router(logs.router, prefix="/api/v1", tags=["logs"])
app.include_router(hermes.router, prefix="/api/v1/hermes", tags=["hermes"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "SLM-Forge API", "version": "0.6.0", "docs": "/docs"}


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys
    return HealthResponse(
        status="ok",
        version="0.6.0",
        phase="Phase 5 — maintenance + polish",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": True,
            "autoresearch": True,
            "ingestion": True,
            "export_gguf": True,
            "hermes_bridge": True,
        },
    )
