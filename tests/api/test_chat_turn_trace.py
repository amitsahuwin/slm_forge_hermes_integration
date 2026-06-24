"""Each chat turn writes one ``HermesTrace`` row with source='chat:turn'.

Reuses the existing Hermes trace table so the chat turns appear in the
Skill-Activity view of the Traces tab — no new UI, no new endpoint.
Operators get one place to ask "what is the agent doing right now?".
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.chat import ChatConversation
from apps.api.models.hermes_trace import HermesTrace
from apps.api.routers import chat as chat_router
from apps.api.services import db as db_module


@pytest.fixture()
def engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def test_record_chat_turn_writes_hermes_trace(engine) -> None:
    """``_record_chat_turn_trace`` adds one row with the expected metadata."""
    with Session(engine) as db:
        c = ChatConversation(title="t", tenant_id="acme", user_id="alice")
        db.add(c)
        db.commit()
        db.refresh(c)
        chat_router._record_chat_turn_trace(
            conversation_id=c.id or 0,
            message_count_in=5,
            summarization_fired=True,
            final_text="here is your answer",
            tool_result_count=2,
            duration_ms=1234,
            error=None,
        )
        rows = db.exec(select(HermesTrace)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "chat:turn"
    assert row.duration_ms == 1234
    assert row.error is None
    assert row.success is True
    # request_body / response_body are JSON serialised metadata, not literal
    # payloads (no PII risk from chat content here).
    import json

    req = json.loads(row.request_body)
    assert req["conversation_id"] == (c.id or 0)
    assert req["message_count_in"] == 5
    assert req["summarization_fired"] is True
    resp = json.loads(row.response_body)
    assert resp["tool_result_count"] == 2


def test_chat_turn_trace_marks_failure(engine) -> None:
    """When the turn raises, the trace row carries ``error`` + ``success=False``."""
    with Session(engine) as db:
        c = ChatConversation(title="t", tenant_id="default")
        db.add(c)
        db.commit()
        db.refresh(c)
        chat_router._record_chat_turn_trace(
            conversation_id=c.id or 0,
            message_count_in=3,
            summarization_fired=False,
            final_text="",
            tool_result_count=0,
            duration_ms=99,
            error="ollama unreachable",
        )
        row = db.exec(select(HermesTrace)).one()
    assert row.error == "ollama unreachable"
    assert row.success is False


def test_chat_turn_trace_tenant_carries_from_convo(engine) -> None:
    """The trace row inherits the conversation's tenant — so the Traces
    tab tenant filter works for chat:turn rows too."""
    with Session(engine) as db:
        c = ChatConversation(title="t", tenant_id="acme")
        db.add(c)
        db.commit()
        db.refresh(c)
        chat_router._record_chat_turn_trace(
            conversation_id=c.id or 0,
            message_count_in=1,
            summarization_fired=False,
            final_text="hi",
            tool_result_count=0,
            duration_ms=10,
            error=None,
        )
        row = db.exec(select(HermesTrace)).one()
    assert row.tenant_id == "acme"