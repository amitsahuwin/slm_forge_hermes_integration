"""Audit trail for every captured error + (PR-B) every auto-fix attempt.

One row per logical capture event. The ``status`` column lets the UI
(and ops scripts) distinguish:

  - ``reported``    — error captured, GitHub issue opened/commented.
  - ``proposed``    — dev-mode SDK call produced a candidate fix (PR-B).
  - ``applied``     — SDK Edit landed on the sandbox branch (PR-B).
  - ``verified``    — pytest + ruff + mypy green on the sandbox (PR-B).
  - ``deployed``    — commit landed + uvicorn reload signalled (PR-B).
  - ``rejected``    — failed a preflight gate (denylist, dirty tree, …).
  - ``failed``      — SDK or pytest failed; escalated to a GitHub issue.

For PR-A only ``reported`` and ``rejected`` are reachable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class AutoFixStatus(str, Enum):  # noqa: UP042 — repo convention (matches RunStatus / ExportStatus)
    REPORTED = "reported"
    PROPOSED = "proposed"
    APPLIED = "applied"
    VERIFIED = "verified"
    DEPLOYED = "deployed"
    REJECTED = "rejected"
    FAILED = "failed"


class AutoFixAttempt(SQLModel, table=True):
    __tablename__ = "auto_fix_attempt"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    # SHA256-derived dedupe key — same exception class + same top frames.
    fingerprint: str = Field(index=True, max_length=64)
    # "production" | "development" — recorded at capture time (not later).
    mode: str = Field(default="production")
    # Where the exception came from: "api" | "api.middleware" | "api.asyncio"
    # | "trainer" | "ratchet" | "exporter".
    source: str = Field(default="api")
    error_type: str = ""
    # Redacted error message. Cap to keep DB rows light.
    error_message: str = Field(default="", max_length=2_000)
    file_target: str | None = None
    branch: str | None = None
    test_path: str | None = None
    status: str = Field(default=AutoFixStatus.REPORTED.value, index=True)
    attempt_count: int = 1
    issue_url: str | None = None
    pr_url: str | None = None
    # Snapshot of ``git diff`` after the SDK Edit lands (PR-B). Capped at
    # 64 KB so a runaway refactor can't bloat the table.
    diff: str | None = Field(default=None, max_length=65_536)
    occurrences_in_window: int = 1

    # Correlation IDs lifted from ``packages._log_context.current()`` at
    # capture time. Useful for cross-referencing with the JSON log stream.
    correlation_request_id: str | None = None
    correlation_run_id: str | None = None
    correlation_session_id: str | None = None

    # PR-1 A4 — multi-tenant ready.
    tenant_id: str = Field(default="default", index=True)

    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
