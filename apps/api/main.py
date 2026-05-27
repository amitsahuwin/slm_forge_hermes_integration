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
