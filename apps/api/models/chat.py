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


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="chat_conversations.id", index=True)
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str = ""
    # JSON-encoded list of tool calls / tool results for assistant messages
    tool_calls_json: str | None = None
    created_at: datetime = Field(default_factory=_now)
