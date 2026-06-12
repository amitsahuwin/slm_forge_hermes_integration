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
    request_body: str = ""
    response_body: str = ""
    # Filled when the call failed before getting any response.
    error: str | None = None
    duration_ms: int = 0
