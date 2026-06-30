"""Ollama request/response trace storage.

Every Hermes/Ollama call goes through ``packages.ratchet.hermes_bridge._call_ollama``;
that function records a row here so the admin "Traces" tab can show what was
actually sent and what came back — purely the JSON bodies, not full server
logs. Useful for debugging prompt regressions and model behavior changes.

The table is intentionally simple. We trim rows older than the most-recent
500 by default (see ``trim_old_traces``) so it never grows unbounded.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class HermesTrace(SQLModel, table=True):
    __tablename__ = "hermes_traces"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    # Free-form label so callers can group by skill/source. Examples:
    #   "skill:propose_hyperparam_mutation", "chat", "research/outline"
    source: str = Field(default="unknown", index=True)
    model: str = Field(default="")
    # Raw JSON bodies as text — kept verbatim so the UI can pretty-print.
    # PR-1 A3: redacted to a placeholder when source ∈ HERMES_TRACE_REDACT_SOURCES
    # or when HERMES_TRACE_STORE_PAYLOADS=false.
    request_body: str = ""
    response_body: str = ""
    # Filled when the call failed before getting any response.
    error: str | None = None
    duration_ms: int = 0
    # PR-1 A1: total attempts including the final one (success or terminal failure).
    attempts: int = Field(default=1)
    # PR-1 A4: tenant boundary. ``"default"`` for single-tenant deployments;
    # populated from the request contextvar in API context, env in worker context.
    tenant_id: str = Field(default="default", index=True)
    # Phase D — user-level boundary. API-side writes derive this from the
    # request identity; worker-side writes derive it from the parent Run
    # (so a worker-issued span is attributed to the user who created the
    # run, not the worker's system identity). server_default matches the
    # migration (TEXT DEFAULT 'default') so raw INSERTs without user_id
    # still satisfy NOT NULL on legacy code paths.
    user_id: str = Field(
        default="default",
        index=True,
        sa_column_kwargs={"server_default": "default"},
    )

    # Skill-Activity view: parsed from ``source`` (``skill:foo`` → ``foo``).
    # Indexed so the Traces tab can group / multi-select filter cheaply.
    skill_name: str | None = Field(default=None, index=True)
    # First 16 hex chars of sha256(skill_markdown) captured at load time.
    # When this differs from the previous trace for the same skill_name,
    # the UI surfaces a "skill content changed" badge.
    skill_sha256: str | None = Field(default=None)
    # Filesystem mtime of the skill file at load time (UTC) — gives a
    # human-readable "last edited" timestamp alongside the hash.
    skill_mtime: datetime | None = Field(default=None)
    # Correlate each Hermes call back to the run / session that triggered it.
    # Read from the existing run_id_ctx / session_id_ctx contextvars in
    # ``packages._log_context``; NULL when unbound (no fabricated default).
    run_id: int | None = Field(default=None, index=True)
    session_id: int | None = Field(default=None, index=True)
    # Materialised ``error is None`` so filtering by success/error doesn't
    # have to inspect the error TEXT column. Index for fast filter.
    # ``server_default`` mirrors the migration's ``DEFAULT 1`` so raw SQL
    # inserts from older code paths land cleanly without a NULL violation.
    success: bool = Field(
        default=True, index=True, sa_column_kwargs={"server_default": "1"}
    )
    # Phase B — trace nesting. ``kind`` says what produced the row;
    # ``trace_id`` groups every span of a single end-to-end invocation;
    # ``parent_span_id`` is NULL for a root span and the parent's
    # ``span_id`` otherwise; ``agent_run_id`` lets an entire agent
    # invocation be retrieved in one query even across child skills.
    kind: str = Field(
        default="skill", index=True, sa_column_kwargs={"server_default": "'skill'"}
    )
    trace_id: str | None = Field(default=None, index=True)
    parent_span_id: str | None = Field(default=None)
    span_id: str | None = Field(default=None)
    agent_run_id: str | None = Field(default=None, index=True)
