"""Dataset synthesis API — expand a seed dataset via local Ollama.

Endpoints (registered under ``/api/v1/synth``):

  POST /start                 -> kick off a new synthesis job
  GET  /jobs                  -> list recent jobs (in-memory)
  GET  /jobs/{job_id}         -> non-streaming job status
  GET  /jobs/{job_id}/stream  -> SSE: progress / done / error

Job state lives in a module-level dict; jobs run as ``asyncio.create_task``.
This is intentional — synthesis is a developer convenience, not a durable
workflow. Restart the API and the job list resets (the on-disk dataset
produced by a completed job persists).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from apps.api.middleware.auth import requires
from apps.api.services.remedy import translate_error
from packages.dataset_synth.engine import detect_format, synthesize
from packages.ratchet.hermes_bridge import HERMES_MODEL, OLLAMA_URL


async def _attach_remedy(exc: HTTPException, endpoint: str, request_obj: Any) -> HTTPException:
    """PR-3 — convert a 4xx ``HTTPException`` with string detail into one with
    ``detail={"message": str, "remedy": str | None}``.

    Non-4xx exceptions and ones that already carry a dict detail pass through
    unchanged. Returns the (possibly new) exception for the caller to raise.
    """
    if not (400 <= exc.status_code < 500):
        return exc
    if not isinstance(exc.detail, str):
        return exc
    remedy = await translate_error(
        exc.detail,
        context={"endpoint": endpoint, "request": request_obj},
    )
    return HTTPException(exc.status_code, detail={"message": exc.detail, "remedy": remedy})

log = logging.getLogger("api.synth")
router = APIRouter()

# Match the convention used by datasets.py / datasets_detail.py.
DATA_ROOT = Path("/app/data/datasets")

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

# Max simultaneous events queued per job (back-pressure if the client lags).
_QUEUE_MAX = 256
# How long the in-memory job registry keeps finished jobs.
_MAX_JOBS = 100


# ─── Schemas ─────────────────────────────────────────────────────────────


class SynthRequest(BaseModel):
    source_dataset: str
    new_dataset: str = Field(..., description="Name of the new dataset directory")
    target_count: int = Field(..., ge=8, le=5000)
    style_guidance: str = ""
    train_ratio: float = 0.80
    valid_ratio: float = 0.15
    canary_ratio: float = 0.05


class SynthStartResponse(BaseModel):
    job_id: str
    source_count: int
    target_count: int


class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    source_dataset: str
    new_dataset: str
    target_count: int
    generated: int
    batch: int
    dropped_total: int
    created_at: str
    completed_at: str | None
    error: str | None
    result: dict[str, Any] | None


# ─── In-memory job registry ──────────────────────────────────────────────


class _Job:
    """Live state for a single synthesis job."""

    def __init__(self, req: SynthRequest, tenant_id: str, user_id: str) -> None:
        self.job_id: str = uuid.uuid4().hex[:12]
        self.req: SynthRequest = req
        # Phase D.3 — caller identity so writes land under the right
        # per-user dir and job-status endpoints can gate by ownership.
        self.tenant_id: str = tenant_id
        self.user_id: str = user_id
        self.status: JobStatus = "queued"
        self.generated: int = 0
        self.batch: int = 0
        self.dropped_total: int = 0
        self.created_at: str = datetime.now(UTC).isoformat()
        self.completed_at: str | None = None
        self.error: str | None = None
        self.result: dict[str, Any] | None = None
        # Async-safe pub/sub for SSE attachers (one queue per subscriber).
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        # Replay buffer so late attachers see history.
        self._history: deque[dict[str, Any]] = deque(maxlen=_QUEUE_MAX)
        self.task: asyncio.Task[None] | None = None

    def snapshot(self) -> JobInfo:
        return JobInfo(
            job_id=self.job_id,
            status=self.status,
            source_dataset=self.req.source_dataset,
            new_dataset=self.req.new_dataset,
            target_count=self.req.target_count,
            generated=self.generated,
            batch=self.batch,
            dropped_total=self.dropped_total,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error=self.error,
            result=self.result,
        )

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAX)
        # Replay everything we've emitted so the late attacher catches up.
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
                # Slow consumer — drop the oldest by reading-then-putting.
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass


_JOBS: dict[str, _Job] = {}


def _prune_jobs() -> None:
    """Bound the in-memory registry to the most recent _MAX_JOBS entries."""
    if len(_JOBS) <= _MAX_JOBS:
        return
    # Drop oldest by created_at.
    by_age = sorted(_JOBS.values(), key=lambda j: j.created_at)
    to_drop = len(_JOBS) - _MAX_JOBS
    for j in by_age[:to_drop]:
        _JOBS.pop(j.job_id, None)


# ─── Helpers ─────────────────────────────────────────────────────────────


def _dataset_dir(name: str) -> Path:
    return DATA_ROOT / name


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _read_seed_records(src: Path) -> list[dict]:
    """Combine train + valid as the seed pool (canary deliberately excluded)."""
    records = _load_jsonl(src / "train.jsonl") + _load_jsonl(src / "valid.jsonl")
    return records


def _clamp_split(
    total: int, train_r: float, valid_r: float, canary_r: float
) -> tuple[int, int, int]:
    """Split ``total`` into (train, valid, canary), enforcing minimum guarantees.

    Guarantees: valid >= 4, canary >= 1. Train gets the remainder.
    """
    valid = max(4, int(round(total * valid_r)))
    canary = max(1, int(round(total * canary_r)))
    train = total - valid - canary
    if train < 1:
        # Pathological tiny totals — shrink valid/canary to keep train >= 1.
        train = 1
        valid = max(4, (total - train) // 2) if total - train >= 5 else max(0, total - train - 1)
        canary = max(0, total - train - valid)
    return train, valid, canary


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_readme(
    out_dir: Path,
    *,
    source: str,
    target_count: int,
    style_guidance: str,
    model: str,
    fmt: str,
    train: int,
    valid: int,
    canary: int,
) -> None:
    ts = datetime.now(UTC).isoformat()
    body = f"""# {out_dir.name}

Synthetic dataset expanded from `{source}` using local Ollama.

- **Source dataset:** `{source}`
- **Format:** `{fmt}`
- **Generator model:** `{model}`
- **Requested count:** {target_count}
- **Final counts:** train={train}, valid={valid}, canary={canary}
- **Generated:** {ts}

## Style guidance

{style_guidance.strip() or "_(none provided)_"}

## Notes

Records were generated by prompting the local model with 3-5 few-shot examples
sampled from the source `train.jsonl` + `valid.jsonl`. Outputs were validated
against the source schema and deduplicated by canonical-JSON hash. The split
was produced with `random.seed(42)` for reproducibility.
"""
    (out_dir / "README.md").write_text(body, encoding="utf-8")


# ─── Worker coroutine ────────────────────────────────────────────────────


async def _run_synth_job(job: _Job) -> None:
    """The actual synthesis worker. Runs as an asyncio.Task."""
    from apps.api.services.identity import Identity
    from apps.api.services.identity_paths import (
        resolve_dataset,
        safe_name,
        user_datasets_dir,
    )

    req = job.req
    # Phase D.3 — paths derive from the *job owner's* identity, not
    # the worker's. Build a non-admin Identity from the job's tenant/user
    # so resolve_dataset() walks the right dirs.
    owner = Identity(
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        role="data_engineer",
        email=None,
        scopes=frozenset(),
        is_admin=False,
        is_worker=False,
    )
    src_dir = resolve_dataset(req.source_dataset, owner)
    if src_dir is None or not src_dir.is_dir():
        job.status = "error"
        job.error = f"Source dataset {req.source_dataset!r} no longer accessible"
        job.publish("error", {"message": job.error})
        return
    out_dir = user_datasets_dir(owner) / safe_name(req.new_dataset)
    job.status = "running"
    job.publish(
        "progress",
        {"generated": 0, "target": req.target_count, "batch": 0, "dropped": 0},
    )

    try:
        seeds = _read_seed_records(src_dir)
        if not seeds:
            raise RuntimeError(
                f"Source dataset {req.source_dataset!r} has no readable JSONL records"
            )

        fmt = detect_format(seeds)
        loop = asyncio.get_running_loop()

        def progress_cb(ev: dict[str, int]) -> None:
            """Called from the sync worker thread — marshal back to the loop."""
            job.generated = int(ev.get("generated", job.generated))
            job.batch = int(ev.get("batch", job.batch))
            job.dropped_total += int(ev.get("dropped", 0))
            loop.call_soon_threadsafe(
                job.publish,
                "progress",
                {
                    "generated": job.generated,
                    "target": req.target_count,
                    "batch": job.batch,
                },
            )

        # synthesize() is blocking (httpx.post) — run it on a thread.
        synth_records: list[dict] = await asyncio.to_thread(
            synthesize,
            seeds,
            req.target_count,
            model=HERMES_MODEL,
            ollama_url=OLLAMA_URL,
            style_guidance=req.style_guidance,
            batch_size=10,
            progress_cb=progress_cb,
        )

        if not synth_records:
            raise RuntimeError(
                "Synthesis produced 0 valid records — check Ollama logs / model availability"
            )

        # Shuffle deterministically + split.
        rng = random.Random(42)
        rng.shuffle(synth_records)
        total = len(synth_records)
        n_train, n_valid, n_canary = _clamp_split(
            total, req.train_ratio, req.valid_ratio, req.canary_ratio
        )

        train_recs = synth_records[:n_train]
        valid_recs = synth_records[n_train : n_train + n_valid]
        canary_recs = synth_records[n_train + n_valid : n_train + n_valid + n_canary]

        out_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(out_dir / "train.jsonl", train_recs)
        _write_jsonl(out_dir / "valid.jsonl", valid_recs)
        if canary_recs:
            _write_jsonl(out_dir / "canary.jsonl", canary_recs)
        _write_readme(
            out_dir,
            source=req.source_dataset,
            target_count=req.target_count,
            style_guidance=req.style_guidance,
            model=HERMES_MODEL,
            fmt=fmt,
            train=len(train_recs),
            valid=len(valid_recs),
            canary=len(canary_recs),
        )

        job.result = {
            "new_dataset": req.new_dataset,
            "train": len(train_recs),
            "valid": len(valid_recs),
            "canary": len(canary_recs),
            "total": total,
            "format": fmt,
        }
        job.status = "completed"
        job.completed_at = datetime.now(UTC).isoformat()
        job.publish("done", job.result)
        log.info(
            "Synth job %s completed: %s train=%d valid=%d canary=%d",
            job.job_id,
            req.new_dataset,
            len(train_recs),
            len(valid_recs),
            len(canary_recs),
        )
    except Exception as e:
        log.exception("Synth job %s failed", job.job_id)
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        job.completed_at = datetime.now(UTC).isoformat()
        job.publish("error", {"message": job.error})


# ─── Routes ──────────────────────────────────────────────────────────────


@router.post("/start", response_model=SynthStartResponse)
@requires("create", "dataset")
async def start_synth(req: SynthRequest, request: Request) -> SynthStartResponse:
    # PR-3 — wrap the validation block so any 4xx string-detail raise is
    # re-issued with a Hermes-generated remedy. The asyncio.create_task below
    # never raises HTTPException so it's intentionally outside the try.
    # Phase D.3 — resolve source against the caller's visible dirs;
    # output lands under the caller's per-user dir.
    from apps.api.services.identity import current_identity
    from apps.api.services.identity_paths import (
        resolve_dataset,
        safe_name,
        user_datasets_dir,
    )

    identity = current_identity(request)
    try:
        try:
            src_dir = resolve_dataset(req.source_dataset, identity)
        except ValueError as e:
            raise HTTPException(400, f"Invalid source_dataset: {e}") from e
        if src_dir is None or not src_dir.is_dir():
            raise HTTPException(404, f"Source dataset {req.source_dataset!r} not found")

        try:
            new_name = safe_name(req.new_dataset)
        except ValueError as e:
            raise HTTPException(400, f"Invalid new_dataset name: {e}") from e
        out_dir = user_datasets_dir(identity) / new_name
        if out_dir.exists():
            raise HTTPException(409, f"Dataset {req.new_dataset!r} already exists")

        ratio_sum = req.train_ratio + req.valid_ratio + req.canary_ratio
        if abs(ratio_sum - 1.0) > 0.01:
            raise HTTPException(
                400, f"Ratios must sum to 1.0 ± 0.01 (got {ratio_sum:.3f})"
            )
        if any(r < 0 for r in (req.train_ratio, req.valid_ratio, req.canary_ratio)):
            raise HTTPException(400, "Ratios must be non-negative")
        if req.target_count < 8:
            raise HTTPException(400, "target_count must be >= 8")
        if req.valid_ratio * req.target_count < 4:
            raise HTTPException(
                400,
                "valid split would be < 4 records; raise valid_ratio or target_count",
            )

        source_count = _count_jsonl(src_dir / "train.jsonl") + _count_jsonl(
            src_dir / "valid.jsonl"
        )
        if source_count == 0:
            raise HTTPException(
                400, f"Source dataset {req.source_dataset!r} has no train/valid records"
            )
    except HTTPException as e:
        raise await _attach_remedy(
            e, endpoint="POST /api/v1/synth/start", request_obj=req.model_dump()
        ) from None

    job = _Job(req, tenant_id=identity.tenant_id, user_id=identity.user_id)
    _JOBS[job.job_id] = job
    _prune_jobs()
    job.task = asyncio.create_task(_run_synth_job(job))

    return SynthStartResponse(
        job_id=job.job_id,
        source_count=source_count,
        target_count=req.target_count,
    )


def _job_visible(j: _Job, identity) -> bool:
    """Phase D.3 — gate synth job access by tenant + ownership."""
    if j.tenant_id != identity.tenant_id:
        return False
    if identity.is_admin:
        return True
    return j.user_id == identity.user_id


@router.get("/jobs", response_model=list[JobInfo])
def list_jobs(request: Request) -> list[JobInfo]:
    from apps.api.services.identity import current_identity

    identity = current_identity(request)
    jobs = sorted(
        (j for j in _JOBS.values() if _job_visible(j, identity)),
        key=lambda j: j.created_at,
        reverse=True,
    )[:20]
    return [j.snapshot() for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobInfo)
def get_job(job_id: str, request: Request) -> JobInfo:
    from apps.api.services.identity import current_identity

    identity = current_identity(request)
    j = _JOBS.get(job_id)
    if j is None or not _job_visible(j, identity):
        raise HTTPException(404, "Job not found")
    return j.snapshot()


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> EventSourceResponse:
    """SSE stream of progress / done / error events for a job.

    Late attachers receive the full replay buffer first, then live events.
    """
    from apps.api.services.identity import current_identity

    identity = current_identity(request)
    j = _JOBS.get(job_id)
    if j is None or not _job_visible(j, identity):
        raise HTTPException(404, "Job not found")

    async def gen() -> AsyncGenerator[dict[str, str], None]:
        q = j.subscribe()
        try:
            # If the job is already finished, drain replay + close.
            terminal = j.status in {"completed", "failed", "cancelled"}
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                except TimeoutError:
                    # No new events — check if we're done.
                    if j.status in {"completed", "failed", "cancelled"} and q.empty():
                        return
                    continue
                yield {"event": ev["event"], "data": json.dumps(ev["data"])}
                if ev["event"] in {"done", "error"}:
                    return
                # If we started in terminal state, drain and exit.
                if terminal and q.empty():
                    return
        finally:
            j.unsubscribe(q)

    return EventSourceResponse(gen())
