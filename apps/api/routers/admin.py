"""Maintenance endpoints: disk usage + cleanup sweeps."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.models.export import Export
from apps.api.models.run import Run
from apps.api.models.session import SessionStatus, TrainingSession
from apps.api.services.db import get_session

router = APIRouter()

# Paths inside the API container map to host paths via docker-compose volumes
DATA_ROOT     = Path("/app/data")
RUNS_ROOT     = Path("/app/runs")
EXPORTS_ROOT  = Path("/app/exports")

SessionDep = Annotated[Session, Depends(get_session)]


class DiskUsage(BaseModel):
    label: str
    path: str
    bytes: int
    items: int


class DiskUsageResponse(BaseModel):
    entries: list[DiskUsage]
    total_bytes: int


def _dir_size(p: Path) -> tuple[int, int]:
    """Returns (total_bytes, item_count) — robust to permission errors."""
    total = 0
    items = 0
    if not p.exists():
        return 0, 0
    try:
        for sub in p.iterdir():
            if sub.is_symlink():
                continue
            items += 1
            if sub.is_file():
                try:
                    total += sub.stat().st_size
                except OSError:
                    pass
            elif sub.is_dir():
                sub_bytes, _ = _dir_size(sub)
                total += sub_bytes
    except OSError:
        pass
    return total, items


@router.get("/disk-usage", response_model=DiskUsageResponse)
def disk_usage() -> DiskUsageResponse:
    entries = []
    for label, path in [
        ("Runs",       RUNS_ROOT),
        ("Exports",    EXPORTS_ROOT),
        ("Datasets",   DATA_ROOT / "datasets"),
        ("Ingest staging", DATA_ROOT / ".ingest_staging"),
    ]:
        b, n = _dir_size(path)
        entries.append(DiskUsage(label=label, path=str(path), bytes=b, items=n))
    total = sum(e.bytes for e in entries)
    return DiskUsageResponse(entries=entries, total_bytes=total)


class CleanupPlan(BaseModel):
    rejected_runs: list[int]
    bytes_freed_estimate: int
    description: str


class CleanupResponse(BaseModel):
    deleted_run_ids: list[int]
    bytes_freed: int


@router.get("/cleanup/plan", response_model=CleanupPlan)
def cleanup_plan(db: SessionDep) -> CleanupPlan:
    """Show what 'cleanup rejected iterations' would delete WITHOUT touching anything."""
    rejected = _find_rejected_runs(db)
    bytes_estimate = 0
    for run_id in rejected:
        run_dir = RUNS_ROOT / str(run_id)
        if run_dir.exists():
            b, _ = _dir_size(run_dir)
            bytes_estimate += b
    return CleanupPlan(
        rejected_runs=rejected,
        bytes_freed_estimate=bytes_estimate,
        description=(
            "Will delete the on-disk artifacts (adapters, logs, configs) for "
            "rejected iterations of COMPLETED sessions only. The DB rows stay so "
            "you can still see the experiment history. Running sessions and "
            "winners are never touched. Exports are never touched."
        ),
    )


@router.post("/cleanup/execute", response_model=CleanupResponse)
def cleanup_execute(db: SessionDep) -> CleanupResponse:
    """Delete on-disk artifacts for rejected runs from completed sessions."""
    rejected = _find_rejected_runs(db)
    deleted = []
    bytes_freed = 0
    for run_id in rejected:
        run_dir = RUNS_ROOT / str(run_id)
        if not run_dir.exists():
            continue
        b, _ = _dir_size(run_dir)
        try:
            shutil.rmtree(run_dir)
            deleted.append(run_id)
            bytes_freed += b
        except OSError:
            continue
    return CleanupResponse(deleted_run_ids=deleted, bytes_freed=bytes_freed)


def _find_rejected_runs(db: Session) -> list[int]:
    """Find rejected runs (was_accepted=False) of completed sessions only.

    Excludes:
      • Runs from running sessions (still in progress)
      • Winners (was_accepted=True)
      • Standalone runs (session_id is None) — those are user-initiated, never auto-touch
      • Runs that have an Export (would orphan the export)
    """
    # Completed sessions
    sessions = list(db.exec(
        select(TrainingSession).where(TrainingSession.status == SessionStatus.COMPLETED)
    ).all())
    if not sessions:
        return []
    session_ids = [s.id for s in sessions]

    # Rejected runs in those sessions
    rejected = list(db.exec(
        select(Run).where(
            Run.session_id.in_(session_ids),
            Run.was_accepted == False,  # noqa: E712
        )
    ).all())

    # Filter out any with exports
    rejected_ids: list[int] = []
    for r in rejected:
        has_export = db.exec(
            select(Export).where(Export.run_id == r.id).limit(1)
        ).first()
        if not has_export:
            rejected_ids.append(r.id)
    return rejected_ids
