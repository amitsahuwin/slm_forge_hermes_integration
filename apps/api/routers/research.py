"""Market research API — Ollama-driven report generation + browsing.

Endpoints (registered under ``/api/v1/research``):

  POST   /start                 -> kick off a new research job
  GET    /jobs/{job_id}         -> non-streaming snapshot
  GET    /jobs/{job_id}/stream  -> SSE: progress / done / error
  GET    /reports               -> list all .md reports (frontmatter parsed)
  GET    /reports/{filename}    -> raw markdown body
  DELETE /reports/{filename}    -> delete a report file

Job state lives in a module-level dict and runs as ``asyncio.create_task``
— same pattern as ``synth.py``. The on-disk markdown report produced by a
completed job is the durable artifact; in-memory jobs are best-effort.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from packages.ratchet.hermes_bridge import HERMES_MODEL, OLLAMA_URL
from packages.research.engine import (
    Depth,
    _REPORTS_DIR,
    build_report,
    report_path,
)

log = logging.getLogger("api.research")
router = APIRouter()

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

_QUEUE_MAX = 256
_MAX_JOBS = 100

# Path-traversal guard for filenames coming in via URL.
_FILENAME_RE = re.compile(r"^[a-zA-Z0-9\-_.]+\.md$")


# ─── Schemas ─────────────────────────────────────────────────────────────


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    depth: Depth = "standard"


class ResearchStartResponse(BaseModel):
    job_id: str


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    topic: str
    depth: str
    stage: str | None
    section_index: int | None
    section_total: int | None
    section_title: str | None
    created_at: str
    completed_at: str | None
    error: str | None
    filename: str | None
    bytes: int | None


class ReportRow(BaseModel):
    filename: str
    title: str
    topic: str
    depth: str
    generated_at: str
    tags: list[str]
    bytes: int


class ReportContent(BaseModel):
    filename: str
    markdown: str


# ─── In-memory job registry ──────────────────────────────────────────────


class _Job:
    def __init__(self, req: ResearchRequest) -> None:
        self.job_id: str = uuid.uuid4().hex[:12]
        self.req: ResearchRequest = req
        self.status: JobStatus = "queued"
        self.created_at: str = datetime.now(UTC).isoformat()
        self.completed_at: str | None = None
        self.error: str | None = None
        self.stage: str | None = None
        self.section_index: int | None = None
        self.section_total: int | None = None
        self.section_title: str | None = None
        self.filename: str | None = None
        self.bytes: int | None = None
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=_QUEUE_MAX)
        self.task: asyncio.Task[None] | None = None

    def snapshot(self) -> JobInfo:
        return JobInfo(
            job_id=self.job_id,
            status=self.status,
            topic=self.req.topic,
            depth=self.req.depth,
            stage=self.stage,
            section_index=self.section_index,
            section_total=self.section_total,
            section_title=self.section_title,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error=self.error,
            filename=self.filename,
            bytes=self.bytes,
        )

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        for ev in self._history:
            q.put_nowait(ev)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        self._history.append(payload)
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:  # noqa: BLE001
                    pass


_JOBS: dict[str, _Job] = {}


def _prune_jobs() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    by_age = sorted(_JOBS.values(), key=lambda j: j.created_at)
    for j in by_age[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(j.job_id, None)


# ─── Frontmatter parsing ────────────────────────────────────────────────


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(body: str) -> dict[str, Any]:
    """Tiny YAML-ish parser: handles ``key: "value"`` and ``tags: [a, b]``.

    Tolerant — returns ``{}`` for missing frontmatter. We don't pull in
    PyYAML to keep the dep footprint where it is.
    """
    m = _FRONTMATTER_RE.match(body)
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, Any] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            out[key] = ""
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items: list[str] = []
            for tok in _split_yaml_list(inner):
                items.append(_unquote(tok.strip()))
            out[key] = items
        else:
            out[key] = _unquote(val)
    return out


def _split_yaml_list(s: str) -> list[str]:
    """Split a comma-separated YAML inline list, respecting double-quoted strings."""
    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    escape = False
    for ch in s:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quote:
            buf.append(ch)
            escape = True
            continue
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch == "," and not in_quote:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x for x in out if x.strip()]


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


# ─── Worker coroutine ────────────────────────────────────────────────────


async def _run_research_job(job: _Job) -> None:
    req = job.req
    job.status = "running"
    job.publish("progress", {"stage": "starting"})

    try:
        loop = asyncio.get_running_loop()

        def progress_cb(ev: dict[str, Any]) -> None:
            # Called from the worker thread — marshal back to the loop.
            stage = ev.get("stage")
            if isinstance(stage, str):
                job.stage = stage
            if "title" in ev and isinstance(ev["title"], str):
                job.section_title = ev["title"]
            if "index" in ev and isinstance(ev["index"], int):
                job.section_index = ev["index"]
            if "total" in ev and isinstance(ev["total"], int):
                job.section_total = ev["total"]
            loop.call_soon_threadsafe(job.publish, "progress", ev)

        markdown = await asyncio.to_thread(
            build_report,
            req.topic,
            req.depth,
            HERMES_MODEL,
            OLLAMA_URL,
            progress_cb,
        )

        # Persist to disk. Engine intentionally doesn't mkdir; we do it here
        # so the router owns all filesystem side effects.
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = report_path(_REPORTS_DIR, req.topic)
        out_path.write_text(markdown, encoding="utf-8")

        job.filename = out_path.name
        job.bytes = out_path.stat().st_size
        job.status = "completed"
        job.completed_at = datetime.now(UTC).isoformat()
        job.publish(
            "done",
            {
                "filename": out_path.name,
                "path": str(out_path),
                "bytes": job.bytes,
            },
        )
        log.info(
            "Research job %s completed: %s (%d bytes)",
            job.job_id,
            out_path.name,
            job.bytes,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Research job %s failed", job.job_id)
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.completed_at = datetime.now(UTC).isoformat()
        job.publish("error", {"message": job.error})


# ─── Routes ──────────────────────────────────────────────────────────────


@router.post("/start", response_model=ResearchStartResponse)
async def start_research(req: ResearchRequest) -> ResearchStartResponse:
    if req.depth not in ("quick", "standard", "deep"):
        raise HTTPException(400, "depth must be one of: quick, standard, deep")
    if not req.topic.strip():
        raise HTTPException(400, "topic must be non-empty")

    job = _Job(req)
    _JOBS[job.job_id] = job
    _prune_jobs()
    job.task = asyncio.create_task(_run_research_job(job))
    return ResearchStartResponse(job_id=job.job_id)


@router.get("/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str) -> JobInfo:
    j = _JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    return j.snapshot()


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> EventSourceResponse:
    j = _JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "Job not found")

    async def gen() -> AsyncGenerator[dict[str, str], None]:
        q = j.subscribe()
        try:
            terminal = j.status in {"completed", "failed", "cancelled"}
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if j.status in {"completed", "failed", "cancelled"} and q.empty():
                        return
                    continue
                yield {"event": ev["event"], "data": json.dumps(ev["data"])}
                if ev["event"] in {"done", "error"}:
                    return
                if terminal and q.empty():
                    return
        finally:
            j.unsubscribe(q)

    return EventSourceResponse(gen())


@router.get("/reports", response_model=list[ReportRow])
def list_reports() -> list[ReportRow]:
    if not _REPORTS_DIR.exists():
        return []
    rows: list[ReportRow] = []
    for p in _REPORTS_DIR.glob("*.md"):
        try:
            body = p.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("Failed to read %s: %s", p, e)
            continue
        fm = _parse_frontmatter(body)
        tags_raw = fm.get("tags", [])
        tags: list[str] = tags_raw if isinstance(tags_raw, list) else []
        try:
            stat = p.stat()
            size = stat.st_size
            # Fallback timestamp: file mtime → ISO8601 in UTC.
            fallback_ts = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
        except OSError:
            size = 0
            fallback_ts = ""
        rows.append(
            ReportRow(
                filename=p.name,
                title=str(fm.get("title") or p.stem),
                topic=str(fm.get("topic") or ""),
                depth=str(fm.get("depth") or ""),
                generated_at=str(fm.get("generated_at") or fallback_ts),
                tags=[str(t) for t in tags],
                bytes=size,
            )
        )
    rows.sort(key=lambda r: r.generated_at, reverse=True)
    return rows


@router.get("/reports/{filename}", response_model=ReportContent)
def get_report(filename: str) -> ReportContent:
    if not _FILENAME_RE.match(filename):
        raise HTTPException(400, "Invalid filename")
    p = _REPORTS_DIR / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Report not found")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"Read failed: {e}") from e
    return ReportContent(filename=filename, markdown=text)


@router.delete("/reports/{filename}", status_code=204)
def delete_report(filename: str) -> None:
    if not _FILENAME_RE.match(filename):
        raise HTTPException(400, "Invalid filename")
    p = _REPORTS_DIR / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Report not found")
    try:
        p.unlink()
    except OSError as e:
        raise HTTPException(500, f"Delete failed: {e}") from e
