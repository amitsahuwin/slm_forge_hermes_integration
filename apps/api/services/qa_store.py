"""PR-4 — in-memory QA result store for ingest previews.

Holds the (pending | ready | unavailable) status and parsed warnings for
each preview's quality scan. Bounded by both insertion-order LRU eviction
(cap 100) and a TTL (default 30 minutes) so stale previews don't pin
memory after the user navigates away.

v1 limitation (called out in the PR-4 description):
    Per-process state. A multi-API-worker deployment would lose visibility
    across workers; promote to a SQLite ``qa_results`` table when that's
    needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("api.qa_store")

_DEFAULT_CAP = 100
_DEFAULT_TTL_S = 1_800  # 30 minutes


@dataclass
class QAWarning:
    """One row in the quality-review output. Mirrors data_quality_review.md's
    JSON schema with a stable Pydantic-compatible shape for the UI."""

    severity: str  # "low" | "medium" | "high"
    category: str  # "duplicates" | "length_outlier" | "format_mismatch" | …
    message: str
    affected_count: int = 0
    fix: str = ""


@dataclass
class QAResult:
    """The full payload a single QA scan stores."""

    status: str = "pending"  # "pending" | "ready" | "unavailable"
    overall_health: str | None = None  # "good" | "fair" | "poor"
    summary: str | None = None
    warnings: list[QAWarning] = field(default_factory=list)
    ready_to_train: bool | None = None
    error: str | None = None  # populated when status == "unavailable"
    created_at: float = field(default_factory=time.monotonic)


# Process-local state. Treat as an in-memory cache: lost on restart by design.
_STORE: dict[str, QAResult] = {}
_ORDER: list[str] = []  # insertion order — oldest first
_LOCKS: dict[str, asyncio.Lock] = {}


def _ttl_s() -> float:
    return float(os.environ.get("HERMES_QA_CACHE_TTL_S", str(_DEFAULT_TTL_S)))


def _cap() -> int:
    return int(os.environ.get("HERMES_QA_CACHE_CAP", str(_DEFAULT_CAP)))


def _evict_stale() -> None:
    now = time.monotonic()
    ttl = _ttl_s()
    # Snapshot — mutating during iteration is unsafe.
    stale = [k for k in _ORDER if (now - _STORE[k].created_at) > ttl]
    for k in stale:
        _STORE.pop(k, None)
        with contextlib.suppress(ValueError):
            _ORDER.remove(k)
        _LOCKS.pop(k, None)


def _evict_overcap() -> None:
    cap = _cap()
    while len(_ORDER) > cap:
        oldest = _ORDER.pop(0)
        _STORE.pop(oldest, None)
        _LOCKS.pop(oldest, None)


def new_id() -> str:
    """Mint a new opaque qa_id for a preview to advertise."""
    return uuid.uuid4().hex[:12]


def init_pending(qa_id: str) -> None:
    """Stake a slot for ``qa_id`` so the GET endpoint can return ``pending``
    immediately, before the background task starts running."""
    _evict_stale()
    if qa_id not in _STORE:
        _ORDER.append(qa_id)
    _STORE[qa_id] = QAResult(status="pending")
    _evict_overcap()


def lock_for(qa_id: str) -> asyncio.Lock:
    """Per-key lock — two concurrent ``run_qa`` calls for the same id collapse."""
    lock = _LOCKS.get(qa_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[qa_id] = lock
    return lock


def get(qa_id: str) -> QAResult | None:
    _evict_stale()
    return _STORE.get(qa_id)


def set_result(qa_id: str, result: QAResult) -> None:
    """Overwrite the result for ``qa_id`` (preserving its slot in ORDER)."""
    if qa_id not in _STORE:
        _ORDER.append(qa_id)
    _STORE[qa_id] = result
    _evict_overcap()


def clear() -> None:
    """Test helper — wipe the store between cases."""
    _STORE.clear()
    _ORDER.clear()
    _LOCKS.clear()


async def run_qa(qa_id: str, sample_rows: list[dict[str, Any]]) -> None:
    """Entry point used as a FastAPI ``BackgroundTasks`` callback.

    Runs the ``data_quality_review`` skill against a fixed-size slice of
    sample rows (max 50 — keeps the prompt under Ollama's context budget).
    On any failure the slot flips to ``"unavailable"`` rather than raising.
    """
    if not _enabled():
        # Feature-flag off → leave the slot in ``pending`` so the UI shows nothing.
        return

    lock = lock_for(qa_id)
    async with lock:
        existing = _STORE.get(qa_id)
        if existing is not None and existing.status in {"ready", "unavailable"}:
            # Concurrent run already finished — keep the first result.
            return

        from apps.api.services.dataset_qa import analyze

        try:
            result = await asyncio.wait_for(
                analyze(sample_rows[:50]),
                timeout=_timeout_s(),
            )
        except TimeoutError:
            log.info("qa_store run_qa timed out for %s", qa_id)
            set_result(
                qa_id,
                QAResult(
                    status="unavailable",
                    error=f"timed out after {_timeout_s():.0f}s",
                ),
            )
            return
        except Exception as e:
            log.warning("qa_store run_qa failed for %s: %s", qa_id, e)
            set_result(
                qa_id,
                QAResult(status="unavailable", error=f"{type(e).__name__}: {e}"),
            )
            return

        set_result(qa_id, result)


def _enabled() -> bool:
    return os.environ.get("HERMES_QA_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _timeout_s() -> float:
    return float(os.environ.get("HERMES_QA_TIMEOUT_S", "45"))
