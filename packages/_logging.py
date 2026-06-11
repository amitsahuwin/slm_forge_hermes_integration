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

import json
import logging
import logging.handlers
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


class JsonFormatter(logging.Formatter):
    """Emit each LogRecord as a single JSON line.

    Fields (always present): ``ts`` (ISO8601 UTC), ``level``, ``service``,
    ``logger``, ``msg``. Plus any non-None correlation IDs from
    ``packages._log_context.current()``: ``request_id``, ``user_id``,
    ``run_id``, ``session_id``, ``trace_id``. Plus ``exc_info`` (string)
    when an exception is being logged.

    Implementation note: we deliberately avoid the third-party
    ``python-json-logger`` package and use stdlib ``json`` so the
    formatter has zero install footprint for workers that don't pull the
    chat/research extras.
    """

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        # Import lazily to avoid a hard import cycle on partial installs.
        from packages._log_context import current as _ctx_current

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Correlation IDs from contextvars — only emit what's present.
        payload.update(_ctx_current())

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # ``ensure_ascii=False`` keeps unicode readable in logs; default=str
        # lets us serialise datetimes / paths without bespoke encoders.
        return json.dumps(payload, ensure_ascii=False, default=str)


def worker_log_path(worker: str, *, json_format: bool | None = None) -> Path:
    """Canonical path for ``<worker>`` log file.

    Text-format logs go to ``_<worker>.log`` (unchanged from Phase A so the
    LogPane tail keeps working). JSON-format logs go to
    ``_<worker>.log.json`` so Promtail can route them to a different
    pipeline without needing a content sniff.

    Pass ``json_format=None`` (the default) to honour the
    ``SLM_FORGE_LOG_FORMAT`` env var.
    """
    use_json = _resolve_json_format(json_format)
    suffix = ".log.json" if use_json else ".log"
    return RUNS_DIR / f"_{worker}{suffix}"


def _resolve_json_format(json_format: bool | None) -> bool:
    """``json_format`` explicit override → env ``SLM_FORGE_LOG_FORMAT`` → text."""
    if json_format is not None:
        return json_format
    env = os.environ.get("SLM_FORGE_LOG_FORMAT", "text").strip().lower()
    return env == "json"


def setup_worker_logging(
    worker: str,
    *,
    level: int = logging.INFO,
    json_format: bool | None = None,
) -> Path:
    """Attach a rotating file handler for ``worker`` on the root logger.

    Returns the log file path so the caller can print it for the user.
    Safe to call multiple times.

    When ``json_format`` is True (or the ``SLM_FORGE_LOG_FORMAT=json`` env
    var is set), every handler on the root logger — including any stderr
    handler installed by ``logging.basicConfig`` — gets the JsonFormatter
    so the on-disk and stdout streams agree.
    """
    if worker not in VALID_WORKERS:
        raise ValueError(
            f"unknown worker {worker!r}; expected one of {sorted(VALID_WORKERS)}"
        )

    use_json = _resolve_json_format(json_format)
    log_path = worker_log_path(worker, json_format=use_json)

    if worker in _configured:
        return log_path

    # Ensure runs/ exists. The trainer and ratchet workers create per-run
    # subdirs later, but the parent must exist before the handler opens.
    # If the directory is on a read-only mount (e.g. ./runs:/app/runs:ro in
    # docker-compose) this fails — we swallow it and fall back to stderr-only.
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )
    except OSError as exc:
        logging.getLogger(f"slm_forge.{worker}").warning(
            "Could not open %s for write (%s). Worker log will be stderr-only; "
            "the dashboard tail will be empty for this worker. If running in "
            "Docker, ensure ./runs is mounted read-write in docker-compose.yml.",
            log_path,
            exc,
        )
        _configured.add(worker)
        return log_path

    json_formatter = JsonFormatter(service=worker)
    text_formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)
    formatter: logging.Formatter = json_formatter if use_json else text_formatter

    handler.setFormatter(formatter)
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

    # When JSON mode is active, retrofit every existing handler so the
    # stderr stream (logging.basicConfig installs one) also speaks JSON.
    # We keep the existing plain-text Formatter on every handler when JSON
    # is *not* requested — local dev stays readable.
    if use_json:
        for h in root.handlers:
            h.setFormatter(json_formatter)

    _configured.add(worker)
    # Log a marker line so the file isn't empty on first read.
    logging.getLogger(f"slm_forge.{worker}").info(
        "Worker log initialized at %s (format=%s)",
        log_path,
        "json" if use_json else "text",
    )
    return log_path
