"""Chat schema extension for context-aware persistent conversations.

The Traces / Hermes work added a per-trace tenant + skill columns; the
chat tables now need the same multi-tenant boundary plus two
summarization bookkeeping columns and a per-message token estimate.

Pins the contract before model + migration land.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.services import db as db_module


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[ChatConversation.__table__, ChatMessage.__table__],  # type: ignore[arg-type]
    )
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_chat_conversation_has_tenant_user_summary_columns() -> None:
    cols = ChatConversation.__table__.columns  # type: ignore[attr-defined]
    for required in ("tenant_id", "user_id", "summary_message_id", "last_summarized_at"):
        assert required in cols, f"ChatConversation missing column {required!r}"


def test_chat_conversation_index_on_tenant_and_user() -> None:
    cols = ChatConversation.__table__.columns  # type: ignore[attr-defined]
    assert cols["tenant_id"].index, "tenant_id must be indexed (filter every list call)"
    assert cols["user_id"].index, "user_id must be indexed (per-owner queries)"


def test_chat_message_has_tenant_and_token_estimate() -> None:
    cols = ChatMessage.__table__.columns  # type: ignore[attr-defined]
    for required in ("tenant_id", "token_estimate"):
        assert required in cols, f"ChatMessage missing column {required!r}"
    assert cols["tenant_id"].index, "ChatMessage.tenant_id must be indexed"


def test_chat_conversation_tenant_id_default() -> None:
    cols = ChatConversation.__table__.columns  # type: ignore[attr-defined]
    col = cols["tenant_id"]
    assert col.default is not None or col.server_default is not None, (
        "tenant_id needs a default so legacy rows backfill safely"
    )


# ---------------------------------------------------------------------------
# Migration contract
# ---------------------------------------------------------------------------


def test_migration_lists_include_new_columns() -> None:
    convo_cols = {c for c, _ in db_module._CHAT_CONVERSATION_MIGRATIONS}
    for required in ("tenant_id", "user_id", "summary_message_id", "last_summarized_at"):
        assert required in convo_cols, (
            f"_CHAT_CONVERSATION_MIGRATIONS must add {required!r} for legacy DBs"
        )
    msg_cols = {c for c, _ in db_module._CHAT_MESSAGE_MIGRATIONS}
    for required in ("tenant_id", "token_estimate"):
        assert required in msg_cols, (
            f"_CHAT_MESSAGE_MIGRATIONS must add {required!r} for legacy DBs"
        )


def test_migration_applies_to_legacy_tables(tmp_path) -> None:
    """Simulate a pre-spec deployment: bare chat_conversations + chat_messages.
    After ``init_db()``-style migrations, the new columns must land."""
    db_path = tmp_path / "legacy.db"
    eng = create_engine(f"sqlite:///{db_path}")
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE chat_conversations (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'New conversation',
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE chat_messages (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    tool_calls_json TEXT,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO chat_conversations (title, created_at) "
                "VALUES ('legacy', :ts)"
            ),
            {"ts": datetime.now(UTC).isoformat()},
        )
        conn.execute(
            text(
                "INSERT INTO chat_messages (conversation_id, role, content, created_at) "
                "VALUES (1, 'user', 'hi', :ts)"
            ),
            {"ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()

    original_engine = db_module.engine
    try:
        db_module.engine = eng
        db_module._migrate_chat_conversations()
        db_module._migrate_chat_messages()
        with eng.connect() as conn:
            convo_cols = {
                row[1] for row in conn.execute(text("PRAGMA table_info(chat_conversations)"))
            }
            msg_cols = {
                row[1] for row in conn.execute(text("PRAGMA table_info(chat_messages)"))
            }
        for added in ("tenant_id", "user_id", "summary_message_id", "last_summarized_at"):
            assert added in convo_cols, f"convo migration did not add {added!r}"
        for added in ("tenant_id", "token_estimate"):
            assert added in msg_cols, f"message migration did not add {added!r}"
    finally:
        db_module.engine = original_engine


def test_migration_is_idempotent(tmp_path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'idempotent.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[ChatConversation.__table__, ChatMessage.__table__],  # type: ignore[arg-type]
    )
    original_engine = db_module.engine
    try:
        db_module.engine = eng
        db_module._migrate_chat_conversations()
        db_module._migrate_chat_messages()
        # Second run must be a no-op (no ALTER, no error).
        db_module._migrate_chat_conversations()
        db_module._migrate_chat_messages()
    finally:
        db_module.engine = original_engine


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_with_new_columns(isolated_engine) -> None:
    with Session(isolated_engine) as s:
        c = ChatConversation(
            title="t1", tenant_id="acme", user_id="alice", summary_message_id=None
        )
        s.add(c)
        s.commit()
        s.refresh(c)
        s.add(
            ChatMessage(
                conversation_id=c.id or 0,
                role="user",
                content="hello",
                tenant_id="acme",
                token_estimate=2,
            )
        )
        s.commit()
        c2 = s.exec(select(ChatConversation)).one()
        m = s.exec(select(ChatMessage)).one()
        assert c2.tenant_id == "acme"
        assert c2.user_id == "alice"
        assert m.tenant_id == "acme"
        assert m.token_estimate == 2


def test_summary_role_is_accepted(isolated_engine) -> None:
    """``role`` is a free-form string in the model. The spec adds the new
    role ``summary`` for the summarized older-turns marker; it must persist
    + round-trip cleanly."""
    with Session(isolated_engine) as s:
        c = ChatConversation(title="t")
        s.add(c)
        s.commit()
        s.refresh(c)
        s.add(
            ChatMessage(
                conversation_id=c.id or 0,
                role="summary",
                content="(summary of older turns)",
                tenant_id="default",
                token_estimate=42,
            )
        )
        s.commit()
        m = s.exec(select(ChatMessage)).one()
        assert m.role == "summary"
        assert m.content.startswith("(summary")
