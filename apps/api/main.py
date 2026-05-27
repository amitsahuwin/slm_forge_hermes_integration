"""SLM-Forge API — Phase 0 scaffold."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    python: str
    capabilities: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # Phase 1+: initialize SQLite tables, start Huey consumer, register routers.
    yield


app = FastAPI(
    title="SLM-Forge API",
    description="Local-first SLM fine-tuning lab driven by Hermes Agent",
    version="0.1.0",
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


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "SLM-Forge API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys

    return HealthResponse(
        status="ok",
        version="0.1.0",
        phase="Phase 0 — scaffold",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": False,         # Phase 1
            "autoresearch": False,    # Phase 2
            "ingestion": False,       # Phase 3
            "export_gguf": False,     # Phase 4
            "hermes_bridge": False,   # Phase 2
        },
    )
