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

from apps.api.middleware.auth import AuthMiddleware
from apps.api.middleware.error_capture import ErrorCaptureMiddleware
from apps.api.middleware.metrics import PrometheusMiddleware
from apps.api.middleware.request_context import RequestContextMiddleware
from apps.api.routers import (
    admin,
    agents,
    auth,
    chat,
    datasets,
    datasets_detail,
    exports,
    hermes,
    ingest,
    ingest_v2,
    logs,
    metrics,
    models,
    research,
    runs,
    sessions,
    synth,
    traces,
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


class PlatformInfo(BaseModel):
    """Platform detection for smart UI defaults (Phase T)."""
    os: str  # "darwin" or "linux"
    arch: str  # "x86_64" or "arm64"
    has_nvidia_gpu: bool
    default_backend: str  # "mlx" or "cuda"
    platform_label: str  # Human-readable label for UI


def _recover_stranded(db) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Reconcile or fail stranded work — never auto-resume on boot.

    The API never restarts user work on startup. Stranded runs are
    marked FAILED (not re-queued — that auto-pickup is what hammered
    Ollama on every container restart). Sessions stuck at RUNNING are
    reconciled to COMPLETED when their children show a clear winner, or
    transitioned to FAILED with a "rerun manually" message otherwise.

    Returns ``(runs_failed, sessions_touched)``.
    """
    from sqlmodel import select

    from apps.api.models.run import Run, RunStatus
    from apps.api.models.session import SessionStatus, TrainingSession
    from apps.api.services.claims import release_expired_claims

    runs_failed = release_expired_claims(
        db, include_legacy=True, stranded_action="fail",
    )

    running_sessions = db.exec(
        select(TrainingSession).where(
            TrainingSession.status == SessionStatus.RUNNING
        )
    ).all()

    sessions_touched = 0
    terminal_run_states = (
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    )
    for s in running_sessions:
        child_runs = db.exec(
            select(Run).where(Run.session_id == s.id)
        ).all()
        completed = [r for r in child_runs if r.status == RunStatus.COMPLETED]
        all_terminal = all(r.status in terminal_run_states for r in child_runs)

        if completed:
            # Experiment produced at least one successful run; reconcile
            # to COMPLETED instead of restarting. Preserve any best_run_id
            # the ratchet already wrote — only derive one if absent.
            s.status = SessionStatus.COMPLETED
            if s.best_run_id is None:
                with_metric = [
                    r for r in completed if r.final_val_loss is not None
                ]
                if with_metric:
                    best = min(with_metric, key=lambda r: r.final_val_loss or 0.0)
                    s.best_run_id = best.id
                    s.best_metric_value = best.final_val_loss
                else:
                    s.best_run_id = completed[0].id
            s.error_message = None
        elif child_runs and all_terminal:
            s.status = SessionStatus.FAILED
            s.error_message = (
                "All training runs failed before the experiment could "
                "complete. Click Rerun to try again."
            )
        else:
            # No children, or at least one still mid-flight (now-FAILED
            # by release_expired_claims above, but conceptually orphaned).
            s.status = SessionStatus.FAILED
            s.error_message = (
                "Server restarted while this experiment was in progress. "
                "Rerun it manually if you want to continue."
            )
        db.add(s)
        sessions_touched += 1

    db.commit()
    return runs_failed, sessions_touched


def _recover_stranded_runs_and_sessions() -> None:
    """Lifespan wrapper around ``_recover_stranded`` — opens a DB session."""
    from sqlmodel import Session

    from apps.api.services.db import engine

    try:
        with Session(engine) as db:
            runs_failed, sessions_touched = _recover_stranded(db)
            if runs_failed or sessions_touched:
                import logging as _logging
                _logging.getLogger("api.startup").warning(
                    "startup recovery: failed %d run(s), reconciled %d session(s)",
                    runs_failed, sessions_touched,
                )
    except Exception as e:
        # Don't fail startup over this. Log and move on.
        import logging as _logging
        _logging.getLogger("api.startup").warning(
            "startup recovery: skipped (%s)", e, exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_worker_logging("api")
    init_db()
    _recover_stranded_runs_and_sessions()

    # PR-A — wire the error-responder. Validates settings fail-fast (raises
    # RuntimeError when DEPLOYMENT_MODE=production without GITHUB_TOKEN, etc.).
    import asyncio as _asyncio

    from packages.error_responder import capture
    from packages.error_responder import config as _err_config

    _err_config.get_settings()  # fail-fast
    capture.set_service_version(API_VERSION)
    capture.start_dispatcher()

    # asyncio.create_task exceptions are silently swallowed by default. Install
    # a handler so escapes from background tasks (e.g. _run_synth_job) reach
    # the same reporter as request-path errors.
    def _asyncio_exc_handler(loop, context):  # type: ignore[no-untyped-def]
        exc = context.get("exception")
        if exc is not None:
            try:
                capture.report_exception(exc, source="api.asyncio")
            except Exception:
                pass
        loop.default_exception_handler(context)

    _asyncio.get_event_loop().set_exception_handler(_asyncio_exc_handler)

    try:
        yield
    finally:
        await capture.stop_dispatcher()


app = FastAPI(title="SLM-Forge API", version="0.6.0", lifespan=lifespan)

# Middleware order is **load-bearing**.
#
# Starlette wraps middleware in REVERSE-of-add order: the LAST one added is
# OUTERMOST on the request flow. So we add inner ones first and CORS last.
#
# Why CORS must be OUTERMOST:
#   If AuthMiddleware returns 401/403/503 early (e.g. missing token, OPA
#   denial, JWKS unreachable), the response must still carry
#   `Access-Control-Allow-Origin`. Otherwise the browser swallows the
#   response and the page sees an opaque "Failed to fetch" instead of a
#   readable 401 with detail. CORSMiddleware only adds headers to responses
#   that flow THROUGH it — so it must be the outermost layer.
#
# Why CORS also handles OPTIONS preflight first:
#   Browsers send an OPTIONS preflight with no Authorization header before
#   any request that uses Bearer tokens. If AuthMiddleware ran outer, it
#   would 401 every preflight. With CORS outermost, CORS short-circuits
#   preflight responses without consulting inner middleware.
#
# Order at runtime (request → response): CORS → ErrorCapture → Prometheus →
# RequestContext → AuthMiddleware → routes → AuthMiddleware → RequestContext
# → Prometheus → ErrorCapture → CORS.
#
# PR-A — ErrorCaptureMiddleware sits just inside CORS so it sees *every*
# uncaught exception from the inner middleware stack (Auth/JWKS failures,
# Prometheus accounting bugs, etc.) — exceptions that ``@app.exception_handler``
# does not reach.
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(ErrorCaptureMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# PR-A — global exception handler for anything that escapes a route handler.
# 4xx HTTPExceptions stay on FastAPI's default path; we only report 5xx.
from fastapi import HTTPException as _HTTPException  # noqa: E402
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: _Request, exc: Exception):
    if isinstance(exc, _HTTPException):
        raise exc  # let FastAPI render its default response
    try:
        from packages.error_responder import capture as _capture

        _capture.report_exception(
            exc,
            source="api",
        )
    except Exception:
        pass
    request_id = getattr(request.state, "request_id", "")
    return _JSONResponse(
        {"detail": "Internal Server Error", "request_id": request_id},
        status_code=500,
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
# Phase K — Market research engine
app.include_router(research.router, prefix="/api/v1/research", tags=["research"])
# Phase N.3 — Multi-step Hermes agents
app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
# Admin-only Hermes/Ollama request-response trace inspector.
app.include_router(traces.router, prefix="/api/v1/hermes/traces", tags=["hermes-traces"])
# PR-A — admin-only auto-fix audit trail (reports today; full attempts in PR-B).
from apps.api.routers import autofix as _autofix_router  # noqa: E402

app.include_router(_autofix_router.router, prefix="/api/v1/autofix", tags=["autofix"])
# Phase L — Prometheus metrics scrape endpoint (root /metrics, no prefix).
app.include_router(metrics.router, tags=["observability"])
# Phase M — auth (Keycloak + OPA). The endpoints work in both enforcement
# modes; only /auth/users requires admin role + Keycloak admin creds.
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])


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
    except Exception:
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
    except Exception:
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


@app.get("/api/v1/platform", response_model=PlatformInfo)
async def platform() -> PlatformInfo:
    """Platform detection endpoint for UI defaults (Phase T).

    Returns OS, architecture, GPU availability, and recommended default backend.
    macOS → mlx (Apple Silicon only), Linux + NVIDIA → cuda.

    When running in Docker, reads from env vars (SLM_FORGE_PLATFORM_*) set by
    docker-compose based on host detection. Falls back to in-process detection.
    """
    import os
    import platform as py_platform
    import subprocess

    # Try env vars first (set by docker-compose based on host detection)
    env_os = os.getenv("SLM_FORGE_PLATFORM_OS")
    env_has_nvidia = os.getenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "").lower() == "true"

    if env_os:
        # Use env vars (Docker container mode)
        os_name = env_os.lower()
        arch = os.getenv("SLM_FORGE_PLATFORM_ARCH", py_platform.machine().lower())
        has_nvidia = env_has_nvidia
    else:
        # Fall back to in-process detection (bare-metal mode)
        os_name = py_platform.system().lower()
        arch = py_platform.machine().lower()
        has_nvidia = False
        if os_name == "linux":
            try:
                result = subprocess.run(
                    ["nvidia-smi", "-L"],
                    capture_output=True,
                    timeout=2,
                    check=False
                )
                has_nvidia = result.returncode == 0 and b"GPU" in result.stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                has_nvidia = False

    # Determine default backend
    if os_name == "darwin":
        # macOS → mlx (works on Apple Silicon M1/M2/M3)
        default_backend = "mlx"
        platform_label = f"macOS ({arch})"
    elif os_name == "linux" and has_nvidia:
        # Linux + NVIDIA → cuda
        default_backend = "cuda"
        platform_label = f"Linux ({arch}) + NVIDIA GPU"
    else:
        # Fallback to cuda for Linux without GPU (will fail gracefully)
        default_backend = "cuda"
        platform_label = f"Linux ({arch})"

    return PlatformInfo(
        os=os_name,
        arch=arch,
        has_nvidia_gpu=has_nvidia,
        default_backend=default_backend,
        platform_label=platform_label,
    )
