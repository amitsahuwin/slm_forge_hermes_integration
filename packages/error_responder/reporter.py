"""Capture + dispatch the captured error to the right backend.

API path:
    capture.report_exception(exc, source="api", endpoint="/api/v1/...")

Worker path:
    capture.report_exception_sync(exc, source="trainer")
    capture.flush(timeout=30)

The dispatch never blocks the caller. In production mode the GitHub call
happens on a background asyncio task (started by the API ``lifespan``); in
worker context we fall through to a synchronous publish that runs the
GitHub POST inline because workers don't keep an event loop running.
Either way the call site is one function call away from "fire and
forget" — exceptions inside the reporter are swallowed and logged to
stderr.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from packages._log_context import current as _log_ctx
from packages.error_responder import config as _config
from packages.error_responder import fingerprint as _fp
from packages.error_responder import github_issue as _gh

log = logging.getLogger("error_responder.reporter")


_SERVICE_VERSION = "unknown"


def set_service_version(version: str) -> None:
    """Called once from ``apps/api/main.py`` so issue bodies carry a real
    api_version. Workers leave it at ``"unknown"`` — they're versioned with
    the API container."""
    global _SERVICE_VERSION
    _SERVICE_VERSION = version


@dataclass
class _CaptureEvent:
    """One exception captured at a hook point — what the dispatcher consumes."""

    exc_type: str
    error_message: str  # redacted
    fingerprint: str
    redacted_traceback: str
    source: str
    file_target: str | None
    correlation_ids: dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# Storm protection — bounded deque per fingerprint of recent timestamps.
_storm_windows: dict[str, deque[float]] = {}
_storm_window_s = 60.0


def _under_storm_cap(fingerprint: str, threshold: int) -> tuple[bool, int]:
    """Returns ``(should_dispatch, occurrences_in_window)``.

    ``should_dispatch`` is True for the first occurrence in the window
    AND for every occurrence at-or-below ``threshold``. Once the count
    exceeds ``threshold`` the dispatch is suppressed until the window
    rolls forward.
    """
    now = time.monotonic()
    win = _storm_windows.setdefault(fingerprint, deque(maxlen=10_000))
    # Drop entries older than the window.
    while win and (now - win[0]) > _storm_window_s:
        win.popleft()
    win.append(now)
    count = len(win)
    return (count <= threshold, count)


def reset_storm_state() -> None:
    """Test helper."""
    _storm_windows.clear()


def _ctx_ids() -> dict[str, str]:
    """Snapshot the cross-service correlation IDs at capture time."""
    return _log_ctx() or {}


def _capture(
    exc: BaseException,
    *,
    source: str,
) -> _CaptureEvent | None:
    """Translate a live exception into a redacted ``_CaptureEvent``.

    Returns ``None`` if the reporter is disabled — keeps the call site
    branchless.
    """
    try:
        settings = _config.get_settings()
    except RuntimeError as cfg_exc:
        log.error("error_responder disabled — bad config: %s", cfg_exc)
        return None
    if not settings.enabled:
        return None

    try:
        fingerprint = _fp.fingerprint(exc, settings.project_root)
        traceback_str = _fp.format_traceback(exc, redact_secrets=True)
        msg = _fp.redact(str(exc))[:2_000]
        top = _fp.extract_top_project_frame(
            __import__("traceback").extract_tb(exc.__traceback__),
            settings.project_root,
        )
        file_target = top[0] if top else None
    except Exception as e:
        log.error("error_responder failed to capture exception: %s", e)
        return None

    return _CaptureEvent(
        exc_type=type(exc).__name__,
        error_message=msg,
        fingerprint=fingerprint,
        redacted_traceback=traceback_str,
        source=source,
        file_target=file_target,
        correlation_ids=_ctx_ids(),
    )


def _persist_attempt(event: _CaptureEvent, *, action: str, url: str | None) -> int | None:
    """Persist an ``AutoFixAttempt`` row. Best-effort; failures don't bubble."""
    try:
        from sqlmodel import Session as _Session

        from apps.api.models.autofix import AutoFixAttempt, AutoFixStatus
        from apps.api.services.db import engine
        from apps.api.services.tenant import current_tenant

        settings = _config.get_settings()
        status = (
            AutoFixStatus.REPORTED.value if action != "skipped" else AutoFixStatus.REJECTED.value
        )
        row = AutoFixAttempt(
            fingerprint=event.fingerprint,
            mode=settings.deployment_mode,
            source=event.source,
            error_type=event.exc_type,
            error_message=event.error_message,
            file_target=event.file_target,
            status=status,
            issue_url=url,
            occurrences_in_window=1,
            correlation_request_id=event.correlation_ids.get("request_id"),
            correlation_run_id=event.correlation_ids.get("run_id"),
            correlation_session_id=event.correlation_ids.get("session_id"),
            tenant_id=current_tenant(),
            completed_at=datetime.now(UTC),
        )
        with _Session(engine) as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
    except Exception as e:
        log.debug("autofix attempt persistence skipped (%s)", e)
        return None


def _dispatch_production(event: _CaptureEvent, *, client: httpx.Client | None = None) -> None:
    """Production mode: open or comment on a GitHub issue. Synchronous; the
    caller decided whether to run it on a background task or inline."""
    settings = _config.get_settings()
    if not settings.github_token or not settings.github_repo:
        log.debug("github not configured — recording attempt without remote post")
        _persist_attempt(event, action="skipped", url=None)
        return

    should_dispatch, count = _under_storm_cap(event.fingerprint, settings.storm_threshold)
    if not should_dispatch:
        # Storm: we already opened/commented on this fingerprint within the
        # 60s window. Record the occurrence locally; don't spam GitHub.
        log.info(
            "error_responder storm-suppressed dispatch for fingerprint %s (count=%d)",
            event.fingerprint[:12],
            count,
        )
        _persist_attempt(event, action="skipped", url=None)
        return

    title = f"[auto] {event.exc_type}: {event.error_message[:80]}"
    body = _gh.render_issue_body(
        fingerprint=event.fingerprint,
        service=event.source,
        api_version=_SERVICE_VERSION,
        python_version=sys.version.split()[0],
        os_label=platform.platform(),
        correlation_ids=event.correlation_ids,
        redacted_traceback=event.redacted_traceback,
        occurrence_count=count,
        occurrences=[event.timestamp],
    )
    outcome = _gh.open_or_comment_issue(
        repo=settings.github_repo,
        token=settings.github_token,
        title=title,
        body=body,
        fingerprint=event.fingerprint,
        client=client,
    )
    log.info(
        "error_responder dispatch: action=%s url=%s fp=%s",
        outcome.action,
        outcome.url,
        event.fingerprint[:12],
    )
    _persist_attempt(event, action=outcome.action, url=outcome.url)


# ── Public API ──────────────────────────────────────────────────────────


_queue: asyncio.Queue[_CaptureEvent] | None = None
_dispatcher_task: asyncio.Task[None] | None = None


def _ensure_queue() -> asyncio.Queue[_CaptureEvent]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=1024)
    return _queue


async def _dispatch_loop() -> None:
    """Long-running consumer started by the FastAPI lifespan."""
    q = _ensure_queue()
    while True:
        event = await q.get()
        try:
            await asyncio.to_thread(_dispatch_production, event)
        except Exception as e:
            log.error("dispatcher loop swallowed an error: %s", e)
        finally:
            q.task_done()


def start_dispatcher() -> None:
    """Idempotent — call once from API lifespan after settings validate."""
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    loop = asyncio.get_event_loop()
    _dispatcher_task = loop.create_task(_dispatch_loop())


async def stop_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is None:
        return
    _dispatcher_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await _dispatcher_task
    _dispatcher_task = None


def report_exception(exc: BaseException, *, source: str = "api") -> None:
    """Async-context capture — used by the FastAPI exception handler.

    Enqueues to the dispatcher task. Falls through to sync dispatch when
    no event loop is running (e.g. when called from a sync helper).
    """
    event = _capture(exc, source=source)
    if event is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _dispatch_production(event)
        return
    q = _ensure_queue()
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        log.warning("error_responder queue full — dropping event %s", event.fingerprint[:12])


def report_exception_sync(exc: BaseException, *, source: str = "worker") -> None:
    """Synchronous capture — used by worker ``__main__`` wrappers."""
    event = _capture(exc, source=source)
    if event is None:
        return
    _dispatch_production(event)


def flush(timeout: float = 30) -> None:
    """Best-effort drain of any queued events. Safe to call when no loop
    is running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # sync caller — nothing to drain
    q = _ensure_queue()
    if q.empty():
        return
    deadline = time.monotonic() + timeout
    while not q.empty() and time.monotonic() < deadline:
        # Let the dispatcher process its queue; we don't poll-and-pull here.
        loop.run_until_complete(asyncio.sleep(0.05))
