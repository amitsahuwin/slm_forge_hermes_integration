"""Atomic run claiming + lease management (Phase R).

A *claim* transitions a queued run to ``running`` and records who took it
(``claimed_by`` = "hostname:pid") and when (``claimed_at``). The
compare-and-swap UPDATE guarantees two workers can never claim the same
run, even with concurrent requests.

A claim's *lease* is implicit: the metric stream is the renewal. A
running run is considered abandoned when its last activity — the latest
metric ``recorded_at``, falling back to ``claimed_at`` — is older than
``SLM_FORGE_CLAIM_TIMEOUT_MIN`` (default 60 minutes). Expired claims are
released lazily on every claim attempt and at API startup.

See ``docs/specs/PHASE_R_SPEC.md``.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import update
from sqlmodel import Session, desc, or_, select

from apps.api.models.metric import Metric
from apps.api.models.run import Run, RunStatus

log = logging.getLogger("api.claims")

CLAIM_TIMEOUT_ENV = "SLM_FORGE_CLAIM_TIMEOUT_MIN"
DEFAULT_TIMEOUT_MIN = 60.0


def claim_timeout() -> timedelta:
    raw = os.environ.get(CLAIM_TIMEOUT_ENV, "").strip()
    try:
        minutes = float(raw) if raw else DEFAULT_TIMEOUT_MIN
    except ValueError:
        minutes = DEFAULT_TIMEOUT_MIN
    return timedelta(minutes=minutes)


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes — normalize to aware UTC for comparison."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def last_activity(db: Session, run: Run) -> datetime | None:
    """Latest proof-of-life for a claimed run: newest metric, else claim time."""
    latest_metric = db.exec(
        select(Metric.recorded_at)
        .where(Metric.run_id == run.id)
        .order_by(desc(Metric.recorded_at))
        .limit(1)
    ).first()
    candidates = [t for t in (_utc(run.claimed_at), _utc(latest_metric)) if t is not None]
    return max(candidates) if candidates else None


def release_expired_claims(
    db: Session,
    *,
    include_legacy: bool = False,
    stranded_action: Literal["requeue", "fail"] = "requeue",
) -> int:
    """Sweep abandoned running runs; return how many were touched.

    ``include_legacy=True`` (startup recovery) also processes running
    runs that were never claimed (``claimed_at IS NULL`` — pre-Phase-R
    rows or local crashes mid-transition).

    ``stranded_action`` controls the disposition. ``'requeue'`` (the
    default, used by ``claim_next_run``'s mid-operation sweep) puts the
    run back into the QUEUED pool so a living worker can take over.
    ``'fail'`` (used by API startup recovery) instead marks the run
    FAILED — the API never auto-resumes user work on boot, so the user
    decides whether to rerun.
    """
    now = datetime.now(UTC)
    timeout = claim_timeout()
    released = 0

    running = db.exec(select(Run).where(Run.status == RunStatus.RUNNING)).all()
    for run in running:
        if run.claimed_at is None:
            if not include_legacy:
                continue
            if stranded_action == "fail":
                reason = (
                    "Stranded by API restart — run was 'running' with no "
                    "claim record. Rerun manually if needed."
                )
            else:
                reason = (
                    "Re-queued by startup recovery — run was 'running' with "
                    "no claim record (pre-lease era or crashed mid-transition)."
                )
        else:
            activity = last_activity(db, run)
            if activity is not None and now - activity < timeout:
                continue  # worker is alive (or within its lease)
            if stranded_action == "fail":
                reason = (
                    f"Stranded by API restart — claim lease expired (no "
                    f"activity from '{run.claimed_by}' for over {timeout}). "
                    f"Rerun manually if needed."
                )
            else:
                reason = (
                    f"Re-queued: claim lease expired (no activity from "
                    f"'{run.claimed_by}' for over {timeout}). "
                )
        if stranded_action == "fail":
            run.status = RunStatus.FAILED
        else:
            run.status = RunStatus.QUEUED
        run.claimed_by = None
        run.claimed_at = None
        run.error_message = reason
        db.add(run)
        released += 1

    if released:
        db.commit()
        log.info(
            "%s %d expired/legacy claim(s)",
            "Failed" if stranded_action == "fail" else "Released",
            released,
        )
    return released


def _try_claim(db: Session, run_id: int, worker_id: str, now: datetime) -> bool:
    """Compare-and-swap: claim run_id iff it is still queued."""
    result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.QUEUED)  # type: ignore[arg-type]
        .values(
            status=RunStatus.RUNNING,
            claimed_by=worker_id,
            claimed_at=now,
            started_at=now,
        )
    )
    db.commit()
    return bool(result.rowcount == 1)


def claim_next_run(db: Session, *, backend: str, worker_id: str) -> Run | None:
    """Claim the oldest queued run for ``backend``, or None if queue is empty.

    Runs with ``trainer_backend IS NULL`` (pre-Phase-O rows) belong to mlx.
    """
    release_expired_claims(db)

    backend_cond = Run.trainer_backend == backend
    if backend == "mlx":
        backend_cond = or_(backend_cond, Run.trainer_backend.is_(None))  # type: ignore[union-attr]

    candidate_ids = db.exec(
        select(Run.id)
        .where(Run.status == RunStatus.QUEUED)
        .where(backend_cond)
        .order_by(Run.created_at)  # type: ignore[arg-type]
    ).all()

    now = datetime.now(UTC)
    for run_id in candidate_ids:
        if _try_claim(db, run_id, worker_id, now):
            db.expire_all()
            run = db.get(Run, run_id)
            log.info("Run #%s claimed by %s (backend=%s)", run_id, worker_id, backend)
            return run
    return None
