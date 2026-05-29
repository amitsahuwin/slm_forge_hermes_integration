"""Persistent staging for ingest previews.

Flow:
  1. Source endpoint parses raw rows → writes them to data/.ingest_staging/<id>.jsonl
  2. Preview response includes the staging_id
  3. Finalize endpoint reads the staging file by id and writes the final dataset
  4. Stale stages older than STAGE_TTL_HOURS are auto-cleaned

This survives API restarts (unlike an in-memory cache) and doesn't lose rows
between preview and finalize (which a naive cache approach would).
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

STAGE_DIR = Path("/app/data/.ingest_staging")
STAGE_TTL_HOURS = 24


def _ensure_dir() -> Path:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STAGE_DIR


def _cleanup_stale() -> None:
    """Remove staging files older than STAGE_TTL_HOURS."""
    _ensure_dir()
    cutoff = time.time() - STAGE_TTL_HOURS * 3600
    for f in STAGE_DIR.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def stash(rows: list[dict]) -> str:
    """Write rows to a staging file, return an opaque id."""
    _cleanup_stale()
    sid = secrets.token_urlsafe(12)
    path = STAGE_DIR / f"{sid}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return sid


def read(sid: str) -> list[dict]:
    """Read rows from a staging file by id. Raises KeyError if not found."""
    if not sid or "/" in sid or ".." in sid:
        raise KeyError("invalid staging id")
    path = STAGE_DIR / f"{sid}.jsonl"
    if not path.exists():
        raise KeyError(f"staging {sid} not found (may have expired or been finalized)")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def discard(sid: str) -> None:
    """Remove a staging file (called after successful finalize)."""
    if not sid or "/" in sid or ".." in sid:
        return
    (STAGE_DIR / f"{sid}.jsonl").unlink(missing_ok=True)
