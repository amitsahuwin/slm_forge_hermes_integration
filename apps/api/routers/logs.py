"""Live log streaming for runs and worker processes.

Tails on-disk log files and emits SSE ``log`` events. Mirrors the streaming
pattern used by ``apps/api/routers/runs.py:stream_run``.

Endpoints (registered under ``/api/v1`` prefix):

  GET /runs/{run_id}/logs              -> last N lines of training.log
  GET /runs/{run_id}/logs/stream       -> SSE tail of training.log
  GET /logs/{worker}                   -> last N lines of <worker>'s log
  GET /logs/{worker}/stream            -> SSE tail of <worker>'s log
  GET /ratchet/logs/stream             -> back-compat alias for /logs/ratchet/stream
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.run import Run, RunStatus
from apps.api.services.db import engine, get_session
from apps.api.services.identity import current_identity
from apps.api.services.scoping import scope_query
from packages._logging import VALID_WORKERS, worker_log_path

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

# Repo root: apps/api/routers/logs.py -> parents[3] is repo root.
# (parents[0]=routers, parents[1]=api, parents[2]=apps, parents[3]=repo)
REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "runs"

# Tuning knobs
_POLL_INTERVAL = 0.5
_FILE_WAIT_TIMEOUT = 30.0
_MAX_LINES_PER_STREAM = 5000
_TERMINAL = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}


def _run_log_path(run_id: int) -> Path:
    return RUNS_DIR / str(run_id) / "training.log"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _render_json_log_line(raw: str) -> str:
    """Render a structured JSON log line as a readable plain-text row.

    Falls back to the raw line if it isn't valid JSON. Workers emit JSON
    when SLM_FORGE_LOG_FORMAT=json (the default in our Makefile); the
    dashboard's <LogPane> displays plain text, so we reconstruct a
    ``HH:MM:SS  LEVEL    logger  msg`` format here.
    """
    raw = raw.rstrip("\n")
    if not raw or not raw.startswith("{"):
        return raw
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    ts = obj.get("ts", "")
    # Shorten ISO timestamp → HH:MM:SS for compactness.
    if "T" in ts:
        ts = ts.split("T", 1)[1].split(".", 1)[0].split("+", 1)[0]
    level = obj.get("level", "INFO").ljust(7)[:7]
    logger = obj.get("logger", "")
    msg = obj.get("msg", "")
    # Trailing correlation IDs in a compact form so they're still useful.
    tail_bits: list[str] = []
    for k in ("run_id", "session_id", "request_id"):
        v = obj.get(k)
        if v not in (None, ""):
            tail_bits.append(f"{k}={v}")
    tail = (" [" + " ".join(tail_bits) + "]") if tail_bits else ""
    return f"{ts}  {level}  {logger}  {msg}{tail}"


def _resolve_worker_log_path(worker: str) -> Path:
    """Return whichever of ``_<worker>.log.json`` / ``_<worker>.log`` exists.

    Workers write to ``.log.json`` when SLM_FORGE_LOG_FORMAT=json (the
    Makefile default) and ``.log`` otherwise. The API container's own env
    may not match, so we probe both and prefer the more-recently-modified
    file. Falls back to the JSON path even if neither exists, because
    that's the default expectation in the current configuration.
    """
    base = worker_log_path(worker, json_format=False).with_suffix("")
    json_path = base.with_suffix(".log.json")
    text_path = base.with_suffix(".log")
    candidates = [p for p in (json_path, text_path) if p.exists()]
    if not candidates:
        return json_path  # default expectation, caller handles "missing"
    # If both exist, pick the newest one.
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return last ``n`` lines from ``path``. Empty list if missing.

    If ``path`` ends in ``.log.json``, each line is rendered as
    ``HH:MM:SS LEVEL logger msg`` for human display.
    """
    if not path.exists():
        return []
    is_json = path.suffix == ".json"
    with path.open("r", encoding="utf-8", errors="replace") as f:
        raw_lines = [ln.rstrip("\n") for ln in deque(f, maxlen=n)]
    if is_json:
        return [_render_json_log_line(ln) for ln in raw_lines]
    return raw_lines


def _validate_worker(worker: str) -> Path:
    if worker not in VALID_WORKERS:
        raise HTTPException(
            404,
            f"Unknown worker {worker!r}. Valid: {sorted(VALID_WORKERS)}",
        )
    return _resolve_worker_log_path(worker)


# ─── Run-specific endpoints ───────────────────────────────────────────────

@router.get("/runs/{run_id}/logs")
def get_run_logs(
    run_id: int,
    request: Request,
    session: SessionDep,
    n: int = Query(default=500, ge=1, le=_MAX_LINES_PER_STREAM),
) -> dict[str, list[str]]:
    """Return the last ``n`` lines of ``runs/<run_id>/training.log``."""
    # Phase D — caller must own (or be tenant-admin over) the run; otherwise
    # 404 (opaque). Workers carry is_worker=True → WHERE 1=0 → 404.
    identity = current_identity(request)
    run = session.exec(
        scope_query(select(Run).where(Run.id == run_id), identity, Run)
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {"lines": _tail_lines(_run_log_path(run_id), n)}


async def _tail_file_sse(
    path: Path,
    *,
    is_run_terminal: Callable[[], bool] | None = None,
    wait_for_file: bool = True,
) -> AsyncGenerator[dict[str, str], None]:
    """Generic SSE generator that tails ``path`` and yields ``log`` events.

    ``is_run_terminal`` is an optional zero-arg callable that returns ``True``
    when the upstream producer is finished; the generator will then drain and
    emit a ``done`` event.

    ``wait_for_file`` controls whether we wait up to ``_FILE_WAIT_TIMEOUT`` for
    the file to appear, or just emit an empty stream if missing.
    """
    # 1. Wait for the file to appear (worker logs may not exist if the worker
    # hasn't been started yet — emit a friendly status frame instead of erroring).
    waited = 0.0
    yield {
        "event": "status",
        "data": json.dumps({"path": str(path), "exists": path.exists()}),
    }
    while not path.exists():
        if is_run_terminal and is_run_terminal():
            yield {"event": "done", "data": json.dumps({"reason": "terminal-before-log"})}
            return
        if not wait_for_file or waited >= _FILE_WAIT_TIMEOUT:
            yield {
                "event": "info",
                "data": json.dumps(
                    {"message": f"log not yet created at {path.name} (worker not started?)"}
                ),
            }
            return
        await asyncio.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL

    is_json = path.suffix == ".json"
    emitted = 0
    leftover = ""
    try:
        f = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        yield {"event": "error", "data": json.dumps({"message": str(exc)})}
        return

    try:
        while True:
            chunk = f.read()
            if chunk:
                buf = leftover + chunk
                parts = buf.split("\n")
                leftover = parts.pop()
                for line in parts:
                    rendered = _render_json_log_line(line) if is_json else line
                    yield {
                        "event": "log",
                        "data": json.dumps({"line": rendered, "ts": _now_iso()}),
                    }
                    emitted += 1
                    if emitted >= _MAX_LINES_PER_STREAM:
                        yield {"event": "done", "data": json.dumps({"reason": "max-lines"})}
                        return

            # Check terminal condition AFTER draining current chunk.
            if is_run_terminal and is_run_terminal():
                final = f.read()
                if final:
                    buf = leftover + final
                    parts = buf.split("\n")
                    leftover = parts.pop()
                    for line in parts:
                        rendered = _render_json_log_line(line) if is_json else line
                        yield {
                            "event": "log",
                            "data": json.dumps({"line": rendered, "ts": _now_iso()}),
                        }
                        emitted += 1
                        if emitted >= _MAX_LINES_PER_STREAM:
                            break
                if leftover:
                    rendered = _render_json_log_line(leftover) if is_json else leftover
                    yield {
                        "event": "log",
                        "data": json.dumps({"line": rendered, "ts": _now_iso()}),
                    }
                    leftover = ""
                yield {"event": "done", "data": json.dumps({"reason": "terminal"})}
                return

            await asyncio.sleep(_POLL_INTERVAL)
    finally:
        f.close()


@router.get("/runs/{run_id}/logs/stream")
async def stream_run_logs(run_id: int, request: Request) -> EventSourceResponse:
    """Tail ``runs/<run_id>/training.log`` and stream lines as SSE events.

    Terminates when the Run row transitions to a terminal status or when the
    per-stream line cap is reached.
    """
    identity = current_identity(request)
    with Session(engine) as s:
        run = s.exec(
            scope_query(select(Run).where(Run.id == run_id), identity, Run)
        ).first()
        if not run:
            raise HTTPException(404, "Run not found")

    def _terminal() -> bool:
        with Session(engine) as s:
            r = s.get(Run, run_id)
            if not r:
                return True
            return r.status.value in _TERMINAL

    path = _run_log_path(run_id)
    return EventSourceResponse(
        _tail_file_sse(path, is_run_terminal=_terminal, wait_for_file=True)
    )


# ─── Worker endpoints (api/trainer/exporter/ratchet) ──────────────────────

@router.get("/logs/{worker}")
def get_worker_logs(
    worker: str,
    n: int = Query(default=500, ge=1, le=_MAX_LINES_PER_STREAM),
) -> dict[str, list[str] | str]:
    """Return the last ``n`` lines of the worker's log file."""
    path = _validate_worker(worker)
    return {
        "worker": worker,
        "path": str(path),
        "exists": str(path.exists()),
        "lines": _tail_lines(path, n),
    }


@router.get("/logs/{worker}/stream")
async def stream_worker_log(worker: str) -> EventSourceResponse:
    """Tail ``runs/_<worker>.log`` and stream lines as SSE events.

    No terminal condition — runs until the line cap or client disconnect.
    If the file doesn't exist yet (worker not started), emits an ``info``
    frame and closes cleanly so the UI shows a helpful message rather than a
    spinner that never resolves.
    """
    path = _validate_worker(worker)
    return EventSourceResponse(
        _tail_file_sse(path, is_run_terminal=None, wait_for_file=False)
    )


# ─── Back-compat: keep old /ratchet/logs/stream working ───────────────────

@router.get("/ratchet/logs/stream")
async def stream_ratchet_log_legacy() -> EventSourceResponse:
    """Back-compat alias for ``/logs/ratchet/stream``."""
    return EventSourceResponse(
        _tail_file_sse(_resolve_worker_log_path("ratchet"), is_run_terminal=None, wait_for_file=False)
    )
