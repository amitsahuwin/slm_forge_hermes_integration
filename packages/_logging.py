"""Shared worker logging helper.

Every long-running SLM-Forge process (api, trainer, exporter, ratchet) calls
``setup_worker_logging("<name>")`` at startup. This installs a rotating file
handler at ``<repo>/runs/_<name>.log`` in addition to whatever stderr handler
is already configured by ``logging.basicConfig``.

The frontend's ``LogPane`` tails these files via
``GET /api/v1/logs/{worker}/stream``.

Design notes
------------
- Single source of truth for the log file location (``worker_log_path``) so
  the API and the workers agree, regardless of cwd.
- File handler is attached to the *root* logger so third-party libraries that
  log via ``logging`` (httpx, sqlmodel, uvicorn, etc.) are captured too.
- Idempotent — calling twice from the same process is a no-op.
- Rotating: 10 MB per file, 3 backups. Plenty of headroom for a multi-hour
  training run without blowing the disk.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

# Resolve repo root: this file lives at <repo>/packages/_logging.py
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"

_LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
_DATE_FMT = "%H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 3

VALID_WORKERS = {"api", "trainer", "exporter", "ratchet"}

# Track which workers we've configured in this process to avoid double-add.
_configured: set[str] = set()


def worker_log_path(worker: str) -> Path:
    """Canonical path for ``<worker>`` log file. Used by API and workers."""
    return RUNS_DIR / f"_{worker}.log"


def setup_worker_logging(worker: str, *, level: int = logging.INFO) -> Path:
    """Attach a rotating file handler for ``worker`` on the root logger.

    Returns the log file path so the caller can print it for the user.
    Safe to call multiple times.
    """
    if worker not in VALID_WORKERS:
        raise ValueError(
            f"unknown worker {worker!r}; expected one of {sorted(VALID_WORKERS)}"
        )

    log_path = worker_log_path(worker)

    if worker in _configured:
        return log_path

    # Ensure runs/ exists. The trainer and ratchet workers create per-run
    # subdirs later, but the parent must exist before the handler opens.
    os.makedirs(RUNS_DIR, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT))
    handler.setLevel(level)
    # Tag so we can find + dedupe across reloads (uvicorn --reload).
    handler.set_name(f"slm_forge_worker_file:{worker}")

    root = logging.getLogger()
    # Strip any prior handler we installed (e.g. uvicorn reload).
    for h in list(root.handlers):
        if h.get_name() == handler.get_name():
            root.removeHandler(h)
    root.addHandler(handler)
    # Make sure root level is at least INFO; otherwise our handler stays silent.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    _configured.add(worker)
    # Log a marker line so the file isn't empty on first read.
    logging.getLogger(f"slm_forge.{worker}").info(
        "Worker log initialized at %s", log_path
    )
    return log_path
