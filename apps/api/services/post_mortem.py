"""PR-2 — auto-generated failure post-mortem service.

When a run transitions to ``status=failed``, ``routers.runs.patch_run``
enqueues ``generate_for_run`` via FastAPI ``BackgroundTasks``. That coroutine:

1. Loads the run row + tails the training log.
2. Calls ``hermes_bridge.run_skill("failure_post_mortem", {...})``.
3. Persists the resulting markdown to ``Run.post_mortem`` and to a
   sidecar file at ``runs/<id>/post_mortem.md`` so artifact bundles
   carry it too.
4. Sets ``Run.post_mortem_status`` so UIs (and the GET endpoint) can
   distinguish ``pending``/``ready``/``unavailable``.

Design notes
------------
- **Never block the PATCH.** Hermes can take 10-60s on a 30B model;
  the worker reporting ``status=failed`` must not wait.
- **Idempotent.** Cache key = sha256(error_message + last_log_line).
  Repeat PATCHes (or retry storms) collapse to a single Hermes call.
- **Concurrency-capped.** A module-level ``asyncio.Semaphore`` (default 2)
  prevents 20 concurrent failures from saturating Ollama.
- **Per-run lock.** Two BackgroundTasks for the same run can't race.
- **Ollama outages are non-fatal.** ``status="unavailable"`` is recorded;
  the failure UI degrades but doesn't break.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session

log = logging.getLogger("api.post_mortem")

# Where worker artifacts live on the API host (mirrored in routers.runs.ARTIFACTS_ROOT).
ARTIFACTS_ROOT = Path("/app/runs")

# Cap concurrent skill invocations from this module. Failure storms (e.g. a bad
# checkpoint causing many trainers to crash at once) should not stampede Ollama.
_DEFAULT_CONCURRENCY = 2

# Created lazily so monkeypatches to HERMES_MAX_CONCURRENT in tests take effect.
_semaphore: asyncio.Semaphore | None = None

# In-flight de-dup: one lock per run id. Cleared lazily; keys are small ints.
_locks: dict[int, asyncio.Lock] = {}


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        n = int(os.environ.get("HERMES_MAX_CONCURRENT", str(_DEFAULT_CONCURRENCY)))
        _semaphore = asyncio.Semaphore(max(1, n))
    return _semaphore


def _lock_for(run_id: int) -> asyncio.Lock:
    lock = _locks.get(run_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[run_id] = lock
    return lock


def _enabled() -> bool:
    return os.environ.get("HERMES_POST_MORTEM_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _tail_log(run_id: int, lines: int = 200) -> str:
    """Best-effort tail of the run's training log. Empty string on miss."""
    log_path = ARTIFACTS_ROOT / str(run_id) / "training.log"
    if not log_path.exists():
        return ""
    try:
        with log_path.open("rb") as f:
            # Read the last ~64KB; cheaper than line-buffering for big logs.
            try:
                f.seek(-65_536, os.SEEK_END)
            except OSError:
                f.seek(0)
            chunk = f.read().decode("utf-8", errors="replace")
        all_lines = chunk.splitlines()
        return "\n".join(all_lines[-lines:])
    except OSError as e:
        log.warning("tail_log failed for run %s: %s", run_id, e)
        return ""


def _input_hash(error_message: str | None, log_tail: str) -> str:
    """Cache key — collapse repeated failures with the same signature."""
    last_log_line = log_tail.splitlines()[-1] if log_tail else ""
    payload = f"{error_message or ''}\n{last_log_line}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_sidecar(run_id: int, markdown: str) -> None:
    """Best-effort write of runs/<id>/post_mortem.md so artifact bundles carry it."""
    try:
        d = ARTIFACTS_ROOT / str(run_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "post_mortem.md").write_text(markdown, encoding="utf-8")
    except OSError as e:
        log.warning("sidecar write failed for run %s: %s", run_id, e)


async def generate_for_run(run_id: int) -> None:
    """Entry point used by ``BackgroundTasks.add_task``.

    Safe to call multiple times for the same run — the per-run lock plus
    the input-hash cache de-dup repeated calls.
    """
    if not _enabled():
        log.info("post_mortem skipped (disabled via env) run=%s", run_id)
        return

    # Import lazily so the module is import-safe even when sqlmodel/DB are
    # unavailable (e.g. in narrow unit tests).
    from apps.api.models.run import Run, RunStatus
    from apps.api.services.db import engine

    lock = _lock_for(run_id)
    async with lock:
        # Re-read inside the lock so racing PATCHes see consistent state.
        with Session(engine) as db:
            run = db.get(Run, run_id)
            if run is None:
                log.warning("post_mortem run %s vanished before generation", run_id)
                return
            if run.status != RunStatus.FAILED:
                log.info("post_mortem run %s not in failed state; skipping", run_id)
                return

            log_tail = _tail_log(run_id)
            input_hash = _input_hash(run.error_message, log_tail)

            # Cache hit — same fingerprint already processed for this run.
            if run.post_mortem_input_hash == input_hash and run.post_mortem_status in {
                "ready",
                "unavailable",
            }:
                log.info("post_mortem cache hit run=%s hash=%s", run_id, input_hash[:12])
                return

            # Mark pending so the UI poll endpoint sees the transition.
            run.post_mortem_status = "pending"
            run.post_mortem_input_hash = input_hash
            db.add(run)
            db.commit()
            db.refresh(run)

            # Build the payload the skill expects.
            payload = {
                "run_id": run_id,
                "dataset": run.dataset,
                "base_model": run.base_model,
                "method": str(run.method),
                "trainer_backend": run.trainer_backend,
                "iters": run.iters,
                "batch_size": run.batch_size,
                "learning_rate": run.learning_rate,
                "num_layers": run.num_layers,
                "max_seq_length": run.max_seq_length,
                "error_message": run.error_message,
                "log_tail": log_tail[-8_000:],  # cap inbound size for Hermes
            }

        # Run the LLM call OUTSIDE the DB session — the call can take tens of
        # seconds and holding a Session that long is bad practice.
        sem = _get_semaphore()
        async with sem:
            markdown, status = await _call_skill(payload)

        # Persist outcome.
        with Session(engine) as db:
            run = db.get(Run, run_id)
            if run is None:  # vanished mid-call — bail safely.
                return
            run.post_mortem = markdown
            run.post_mortem_status = status
            run.post_mortem_generated_at = datetime.now(UTC)
            db.add(run)
            db.commit()

        if markdown and status == "ready":
            _write_sidecar(run_id, markdown)


async def _call_skill(payload: dict[str, object]) -> tuple[str, str]:
    """Call the failure_post_mortem skill in a worker thread.

    ``hermes_bridge._call_ollama`` uses synchronous ``httpx`` so we offload
    to a thread to avoid blocking the event loop.

    Returns:
        (markdown, status) — status is ``"ready"`` or ``"unavailable"``.
    """
    from packages.ratchet.hermes_bridge import run_skill

    try:
        markdown = await asyncio.to_thread(
            run_skill,
            "failure_post_mortem",
            payload,
            expect_json=False,
        )
        return markdown, "ready"
    except FileNotFoundError as e:
        log.error("failure_post_mortem skill missing: %s", e)
        return f"[post-mortem unavailable: skill not installed — {e}]", "unavailable"
    except Exception as e:
        # Catch broadly: Hermes outages must not bubble into the API.
        log.warning("post_mortem generation failed: %s", e)
        return f"[post-mortem unavailable: {type(e).__name__}: {e}]", "unavailable"
