"""Token-budget guard for chat history.

When a conversation grows past 75% of the model's context window,
``compose_working_context`` (a pure function in
``packages/chat_agent/history.py``) keeps the last N turns verbatim
and asks the caller's injected summarizer to compress the older tail
into a ``SystemMessage``. The function never touches the DB or HTTP;
callers wire it up.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from packages.chat_agent.history import (
    compose_working_context,
    estimate_tokens,
)


def _user(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _ai(text: str) -> AIMessage:
    return AIMessage(content=text)


def _noop_summarizer(_msgs: list[BaseMessage]) -> str:
    raise AssertionError("summarizer should not be invoked when under budget")


def _const_summarizer(text: str) -> Callable[[list[BaseMessage]], str]:
    return lambda _msgs: text


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_estimate_tokens_for_simple_message() -> None:
    """Cheap heuristic: len(content) // 4 — close enough for budget math
    without loading a tokenizer in the API container."""
    assert estimate_tokens(_user("a" * 40)) == 10


def test_estimate_tokens_for_list() -> None:
    msgs = [_user("a" * 40), _ai("b" * 80)]
    # 10 + 20 = 30
    assert sum(estimate_tokens(m) for m in msgs) == 30


# ---------------------------------------------------------------------------
# Under-budget happy path
# ---------------------------------------------------------------------------


def test_under_budget_returns_messages_unchanged() -> None:
    msgs = [_user("hi"), _ai("hello"), _user("how are you")]
    kept, summary = compose_working_context(
        msgs, max_tokens=1000, keep_last_n=8, summarizer=_noop_summarizer
    )
    assert kept == msgs
    assert summary is None


def test_empty_input_is_handled() -> None:
    kept, summary = compose_working_context(
        [], max_tokens=100, keep_last_n=8, summarizer=_noop_summarizer
    )
    assert kept == []
    assert summary is None


# ---------------------------------------------------------------------------
# Over-budget: summarize the head
# ---------------------------------------------------------------------------


def test_over_budget_keeps_last_n_verbatim_and_summarizes_head() -> None:
    # 12 messages, each ~25 tokens (100 chars / 4). Total ~300.
    msgs: list[BaseMessage] = [_user("x" * 100) for _ in range(12)]
    kept, summary = compose_working_context(
        msgs,
        max_tokens=80,
        keep_last_n=4,
        summarizer=_const_summarizer("(8 older turns)"),
    )
    # Only the last 4 are kept verbatim; the rest are summarised.
    assert len(kept) == 4
    assert kept == msgs[-4:]
    assert isinstance(summary, SystemMessage)
    assert summary.content == "(8 older turns)"


def test_summarizer_receives_only_the_head() -> None:
    """The summarizer is fed the messages it should compress, not the
    kept tail — so a future incremental-summary mode can update the
    rolling summary without re-summarising everything."""
    msgs: list[BaseMessage] = [_user(str(i) * 100) for i in range(10)]
    captured: list[list[BaseMessage]] = []

    def capturing_summarizer(head: list[BaseMessage]) -> str:
        captured.append(list(head))
        return "summary"

    _, _ = compose_working_context(
        msgs, max_tokens=50, keep_last_n=3, summarizer=capturing_summarizer
    )
    assert len(captured) == 1
    assert captured[0] == msgs[:-3]  # everything except the tail


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_keep_last_n_larger_than_history_returns_all() -> None:
    """If keep_last_n >= len(messages), nothing to summarize — return
    everything as kept and no summary."""
    msgs = [_user("a"), _ai("b")]
    kept, summary = compose_working_context(
        msgs, max_tokens=10, keep_last_n=8, summarizer=_noop_summarizer
    )
    assert kept == msgs
    assert summary is None


def test_summarizer_failure_falls_back_gracefully() -> None:
    """When the summarizer raises (e.g. Ollama down), the composer must
    not crash the chat turn — it returns kept_last_n+1 messages (one
    extra) and no summary, so the turn still completes with a degraded
    but functional context. CLAUDE.md §16 (graceful degradation,
    contain errors)."""
    msgs: list[BaseMessage] = [_user("x" * 100) for _ in range(10)]

    def boom(_msgs: list[BaseMessage]) -> str:
        raise RuntimeError("ollama down")

    kept, summary = compose_working_context(
        msgs, max_tokens=20, keep_last_n=3, summarizer=boom
    )
    # On failure we keep a wider window verbatim and skip the summary.
    assert summary is None
    assert kept == msgs[-(2 * 3):]  # 2x keep_last_n as fallback


def test_summary_is_prepended_in_caller_usage() -> None:
    """Smoke: when caller prepends the returned summary to ``kept``, the
    combined length is keep_last_n + 1 — what the LangGraph agent sees
    as its working context."""
    msgs: list[BaseMessage] = [_user("y" * 100) for _ in range(8)]
    kept, summary = compose_working_context(
        msgs, max_tokens=30, keep_last_n=2, summarizer=_const_summarizer("S")
    )
    assert summary is not None
    final = [summary, *kept]
    assert len(final) == 3
    assert isinstance(final[0], SystemMessage)


# ---------------------------------------------------------------------------
# Config-driven max
# ---------------------------------------------------------------------------


def test_max_tokens_must_be_positive() -> None:
    """CLAUDE.md §23 — validate config at startup, fail fast."""
    with pytest.raises(ValueError):
        compose_working_context(
            [_user("hi")], max_tokens=0, keep_last_n=4, summarizer=_noop_summarizer
        )
    with pytest.raises(ValueError):
        compose_working_context(
            [_user("hi")], max_tokens=-1, keep_last_n=4, summarizer=_noop_summarizer
        )