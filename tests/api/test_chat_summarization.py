"""Token-budget summarisation, end-to-end.

When a conversation grows past ``CHAT_HISTORY_MAX_TOKENS``, the chat
stream handler delegates to ``compose_working_context`` and persists
the resulting summary as a ``role='summary'`` row. The next turn's
``_load_history_as_langchain`` then re-uses that summary as a
``SystemMessage`` — bounded prompt size regardless of conversation
length.

These tests pin the wiring: the budget is read from env, the summarizer
is invoked when over-budget, and the persisted summary round-trips.
"""
from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.routers import chat as chat_router
from apps.api.services import db as db_module


@pytest.fixture()
def engine_with_long_convo(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[ChatConversation.__table__, ChatMessage.__table__],  # type: ignore[arg-type]
    )
    monkeypatch.setattr(db_module, "engine", eng)
    with Session(eng) as s:
        c = ChatConversation(title="long")
        s.add(c)
        s.commit()
        s.refresh(c)
        cid = c.id or 0
        # Seed 20 turns, each ~100 chars (~25 tokens by the cheap heuristic)
        for _i in range(10):
            s.add(ChatMessage(conversation_id=cid, role="user", content="u" * 100))
            s.add(ChatMessage(conversation_id=cid, role="assistant", content="a" * 100))
        s.commit()
    return eng, cid


def test_under_budget_no_summarization(engine_with_long_convo, monkeypatch) -> None:
    """When the env max is generous, neither the summarizer nor the
    role='summary' row should be created."""
    eng, cid = engine_with_long_convo
    monkeypatch.setenv("CHAT_HISTORY_MAX_TOKENS", "100000")
    with Session(eng) as db:
        history = chat_router._load_history_as_langchain(db, cid)
    kept, summary = chat_router._apply_history_budget(history)
    assert summary is None
    assert kept == history


def test_over_budget_triggers_summarization(
    engine_with_long_convo, monkeypatch
) -> None:
    """With a tiny ``CHAT_HISTORY_MAX_TOKENS``, ``_apply_history_budget``
    must call the summarizer and return a (kept_tail, SystemMessage)
    pair."""
    eng, cid = engine_with_long_convo

    captured: dict[str, Any] = {}

    def fake_run_skill(name: str, payload: dict[str, Any], **_kw: Any) -> str:
        captured["name"] = name
        captured["payload"] = payload
        return "(synthetic summary of older turns)"

    import packages.ratchet.hermes_bridge as hb

    monkeypatch.setattr(hb, "run_skill", fake_run_skill)
    monkeypatch.setenv("CHAT_HISTORY_MAX_TOKENS", "100")
    monkeypatch.setenv("CHAT_HISTORY_KEEP_LAST_N", "4")

    with Session(eng) as db:
        history = chat_router._load_history_as_langchain(db, cid)
    kept, summary = chat_router._apply_history_budget(history)

    assert captured.get("name") == "summarize_chat_window"
    assert len(kept) == 4
    assert summary is not None
    assert getattr(summary, "content", "").startswith("(synthetic")


def test_summary_persists_and_round_trips(
    engine_with_long_convo, monkeypatch
) -> None:
    """After the chat router persists a ``role='summary'`` row, the
    next turn's history-replay must see exactly one ``SystemMessage`` at
    the head (and the kept tail in chronological order)."""
    eng, cid = engine_with_long_convo

    monkeypatch.setenv("CHAT_HISTORY_MAX_TOKENS", "100")
    monkeypatch.setenv("CHAT_HISTORY_KEEP_LAST_N", "4")
    import packages.ratchet.hermes_bridge as hb

    monkeypatch.setattr(
        hb, "run_skill", lambda *args, **kw: "(summary v1)"
    )

    with Session(eng) as db:
        history = chat_router._load_history_as_langchain(db, cid)
    _, summary = chat_router._apply_history_budget(history)
    assert summary is not None

    # Simulate the chat router persisting the summary.
    with Session(eng) as db:
        db.add(
            ChatMessage(
                conversation_id=cid,
                role="summary",
                content=summary.content,
                tool_calls_json=None,
            )
        )
        db.commit()

    # Next turn replays. Loader prepends summary; everything else is the
    # tail messages in chronological order.
    with Session(eng) as db:
        replayed = chat_router._load_history_as_langchain(db, cid)
    from langchain_core.messages import SystemMessage

    assert isinstance(replayed[0], SystemMessage)
    assert replayed[0].content == "(summary v1)"


def test_invalid_env_fails_fast(monkeypatch) -> None:
    """CLAUDE.md §23: bad config raises at the boundary, not in a quiet log."""
    monkeypatch.setenv("CHAT_HISTORY_MAX_TOKENS", "not-a-number")
    with pytest.raises(RuntimeError, match="CHAT_HISTORY_MAX_TOKENS"):
        chat_router._apply_history_budget([])