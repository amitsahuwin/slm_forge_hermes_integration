"""Full-fidelity history replay for context-aware chat.

The pre-spec code only replayed ``user`` and ``assistant`` rows
(``apps/api/routers/chat.py:L248-252``), explicitly dropping ``tool``
and ``system`` rows. That made the agent forget which tools it had
already called between turns — symptoms: the agent re-fires
``list_runs`` on every turn instead of remembering it already has
the answer.

After this change:
  * ``role='user'``      → ``HumanMessage``
  * ``role='assistant'`` → ``AIMessage`` (carrying parsed
                            ``tool_calls_json`` as ``tool_calls=``)
  * ``role='tool'``      → ``ToolMessage(tool_call_id=..., content=...)``
  * ``role='summary'``   → ``SystemMessage`` prepended once at the top

Order is chronological by ``created_at`` / ``id``.
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.routers.chat import _load_history_as_langchain
from apps.api.services import db as db_module


@pytest.fixture()
def engine_with_convo(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[ChatConversation.__table__, ChatMessage.__table__],  # type: ignore[arg-type]
    )
    monkeypatch.setattr(db_module, "engine", eng)
    with Session(eng) as s:
        convo = ChatConversation(title="t1")
        s.add(convo)
        s.commit()
        s.refresh(convo)
    return eng, convo.id


def _seed(eng, cid: int, rows: list[dict]) -> None:
    with Session(eng) as s:
        for r in rows:
            s.add(ChatMessage(conversation_id=cid, **r))
        s.commit()


# ---------------------------------------------------------------------------
# Role coverage
# ---------------------------------------------------------------------------


def test_user_and_assistant_roundtrip(engine_with_convo) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    kinds = [type(m).__name__ for m in out]
    assert kinds == ["HumanMessage", "AIMessage"]
    assert isinstance(out[0], HumanMessage) and out[0].content == "ping"
    assert isinstance(out[1], AIMessage) and out[1].content == "pong"


def test_tool_messages_are_replayed(engine_with_convo) -> None:
    """When the assistant called a tool and the tool returned a value, the
    next turn must see both the AIMessage(tool_calls=...) and the
    subsequent ToolMessage. Currently the loader drops `tool` rows."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    eng, cid = engine_with_convo
    tc = [{"id": "call_42", "name": "list_runs", "args": {}}]
    _seed(eng, cid, [
        {"role": "user", "content": "show recent runs"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls_json": json.dumps(tc),
        },
        {
            "role": "tool",
            "content": json.dumps([{"id": 7, "status": "completed"}]),
            "tool_calls_json": json.dumps({"tool_call_id": "call_42",
                                           "name": "list_runs"}),
        },
        {"role": "assistant", "content": "Here is run #7."},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    kinds = [type(m).__name__ for m in out]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert isinstance(out[0], HumanMessage)
    assert isinstance(out[1], AIMessage)
    saved_tcs = getattr(out[1], "tool_calls", None) or []
    assert len(saved_tcs) == 1, (
        "AIMessage must carry the parsed tool_calls so the next turn "
        "knows what was already called"
    )
    # LangChain normalises tool_calls and adds a ``type='tool_call'``
    # key; compare just the meaningful fields.
    assert saved_tcs[0]["id"] == "call_42"
    assert saved_tcs[0]["name"] == "list_runs"
    assert saved_tcs[0]["args"] == {}
    assert isinstance(out[2], ToolMessage)
    assert out[2].tool_call_id == "call_42"
    assert "completed" in str(out[2].content)


def test_summary_row_is_prepended_as_system_message(engine_with_convo) -> None:
    """``role='summary'`` rows are the in-place summarisation marker. They
    must appear once, as a ``SystemMessage`` at the head of the list, so
    the LangGraph agent treats them as prior-context background rather
    than a turn."""
    from langchain_core.messages import HumanMessage, SystemMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "summary", "content": "Older turns: user introduced as Pat."},
        {"role": "user", "content": "what's my name?"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    assert isinstance(out[0], SystemMessage)
    assert "Pat" in out[0].content
    assert isinstance(out[1], HumanMessage)


def test_multiple_summary_rows_only_latest_kept(engine_with_convo) -> None:
    """A long conversation may have produced several summaries over time
    (each summarisation supersedes the previous). The loader replays only
    the most recent one so the agent never sees stale or duplicated
    summaries."""
    from langchain_core.messages import SystemMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "summary", "content": "OLD summary"},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        {"role": "summary", "content": "NEWER summary"},
        {"role": "user", "content": "follow-up"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    systems = [m for m in out if isinstance(m, SystemMessage)]
    assert len(systems) == 1
    assert systems[0].content == "NEWER summary"


def test_order_is_chronological(engine_with_convo) -> None:
    """Chronological ``id`` order — the existing behavior; pin so the
    refactor doesn't accidentally reverse it."""
    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    contents = [getattr(m, "content", "") for m in out]
    assert contents == ["a", "b", "c"]


def test_assistant_with_no_tool_calls_has_empty_list(engine_with_convo) -> None:
    """When an assistant row has no ``tool_calls_json``, the reconstructed
    AIMessage must NOT carry a stale tool_calls list — empty/None only."""
    from langchain_core.messages import AIMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    ai = next(m for m in out if isinstance(m, AIMessage))
    tcs = getattr(ai, "tool_calls", None) or []
    assert tcs == [], "no tool calls means an empty tool_calls list"


def test_unknown_role_is_skipped(engine_with_convo) -> None:
    """Forward compatibility: a row with an unknown role (e.g. a future
    ``role='clarification'``) should be skipped rather than raising."""
    from langchain_core.messages import HumanMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "user", "content": "ok"},
        {"role": "unknown_future_role", "content": "should be ignored"},
        {"role": "user", "content": "next"},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    assert [type(m).__name__ for m in out] == ["HumanMessage", "HumanMessage"]
    assert all(isinstance(m, HumanMessage) for m in out)


def test_persist_turn_round_trips_through_loader(engine_with_convo) -> None:
    """End-to-end contract: ``_persist_turn`` writes rows that, on the
    next turn, load back as the correct LangChain sequence:

      AIMessage(tool_calls=[...]) → ToolMessage → ... → AIMessage(final)
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from apps.api.routers.chat import _persist_turn

    eng, cid = engine_with_convo
    tool_results = [
        {
            "tool": "list_runs",
            "tool_call_id": "call_abc",
            "result": [{"id": 42, "status": "completed"}],
        }
    ]
    with Session(eng) as db:
        _persist_turn(db, cid, final_text="Here is run #42.", tool_results=tool_results)

    with Session(eng) as db:
        msgs = _load_history_as_langchain(db, cid)
    kinds = [type(m).__name__ for m in msgs]
    assert kinds == ["AIMessage", "ToolMessage", "AIMessage"]
    first_ai = msgs[0]
    assert isinstance(first_ai, AIMessage)
    saved_tcs = getattr(first_ai, "tool_calls", None) or []
    assert len(saved_tcs) == 1
    assert saved_tcs[0]["id"] == "call_abc"
    assert saved_tcs[0]["name"] == "list_runs"
    tool_msg = msgs[1]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.tool_call_id == "call_abc"
    final_ai = msgs[2]
    assert isinstance(final_ai, AIMessage)
    assert final_ai.content == "Here is run #42."


def test_persist_turn_text_only_writes_single_assistant_row(engine_with_convo) -> None:
    """When the turn produced no tool calls, only one assistant row should
    land — no orphan empty-content assistant rows, no tool rows."""
    from apps.api.routers.chat import _persist_turn

    eng, cid = engine_with_convo
    with Session(eng) as db:
        _persist_turn(db, cid, final_text="just chatting", tool_results=[])

    with Session(eng) as db:
        msgs = _load_history_as_langchain(db, cid)
    assert len(msgs) == 1
    assert msgs[0].content == "just chatting"


def test_tool_row_without_tool_call_id_is_safely_handled(engine_with_convo) -> None:
    """A ``role='tool'`` row whose ``tool_calls_json`` is malformed / missing
    a ``tool_call_id`` should still produce a ToolMessage (with an empty
    tool_call_id) rather than crashing the loader."""
    from langchain_core.messages import ToolMessage

    eng, cid = engine_with_convo
    _seed(eng, cid, [
        {"role": "tool", "content": '{"ok": true}', "tool_calls_json": None},
    ])
    with Session(eng) as db:
        out = _load_history_as_langchain(db, cid)
    assert len(out) == 1
    assert isinstance(out[0], ToolMessage)
    assert out[0].tool_call_id == ""
