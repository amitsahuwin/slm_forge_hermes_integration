"""Chat persistence — conversations & messages for the LangGraph chat UI."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    title: str = "New conversation"
    created_at: datetime = Field(default_factory=_now)
    # Multi-tenant boundary (CLAUDE.md §35). Defaults to the literal
    # ``"default"`` so single-tenant deployments keep working unchanged.
    tenant_id: str = Field(
        default="default", index=True, sa_column_kwargs={"server_default": "'default'"}
    )
    # Conversation owner. When auth is enabled, set from
    # ``request.state.user.id`` at create time. When disabled, stays NULL
    # so the dev-mode workflow continues to be tenant-only.
    user_id: str | None = Field(default=None, index=True)
    # Bookkeeping for the in-place summarization window. ``summary_message_id``
    # is the id of the latest ``role='summary'`` ChatMessage; ``last_summarized_at``
    # is its created_at, used by the per-conversation cooldown so two near-
    # simultaneous turns don't double-summarize.
    summary_message_id: int | None = Field(default=None)
    last_summarized_at: datetime | None = Field(default=None)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="chat_conversations.id", index=True)
    # ``user`` | ``assistant`` | ``system`` | ``tool`` | ``summary``.
    # ``summary`` is the role for an in-place summarisation of older turns;
    # see spec ``CHAT_PERSISTENCE_CONTEXT_AWARE_SPEC.md`` §R2.
    role: str
    content: str = ""
    # JSON-encoded list of tool calls / tool results for assistant messages
    tool_calls_json: str | None = None
    created_at: datetime = Field(default_factory=_now)
    # Denormalized tenant for cheap per-tenant filtering without a join
    # back to ``chat_conversations``.
    tenant_id: str = Field(
        default="default", index=True, sa_column_kwargs={"server_default": "'default'"}
    )
    # Cached token estimate (len(content) // 4) so the working-context
    # composer can do budget math without re-scanning the text on each turn.
    token_estimate: int = Field(default=0)
