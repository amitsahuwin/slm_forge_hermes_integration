"""Token-budget guard + summarisation for chat history.

Pure (no I/O) helpers that the chat router invokes before passing the
working context into the LangGraph agent. Keeping these stateless and
without side-effects makes them cheap to unit-test and easy to reason
about: the only "magic" is the budget math.

Strategy:
  1. Sum a cheap token estimate (``len(content) // 4``) over the
     replayed history.
  2. If the total is under the budget, return the messages unchanged.
  3. Else, keep the last ``keep_last_n`` messages verbatim and ask the
     caller's injected ``summarizer`` callable to compress the rest
     into a single string. The result is wrapped in a
     ``SystemMessage`` so the agent treats it as prior-context
     background.
  4. If the summarizer fails (e.g. Ollama unreachable), fall back to
     keeping a slightly wider tail (2x ``keep_last_n``) verbatim and
     return no summary — the turn still completes (CLAUDE.md §16).

Token estimation is intentionally a heuristic. Loading a real
tokenizer (tiktoken / qwen tokenizer) into the API container costs
~30 MB RAM and ~50 ms cold-start per worker; the heuristic is wrong
by ±25% but the budget is set with that headroom in mind.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.messages import BaseMessage, SystemMessage

log = logging.getLogger("chat_agent.history")

Summarizer = Callable[[list[BaseMessage]], str]


def estimate_tokens(message: BaseMessage) -> int:
    """Cheap heuristic: characters / 4. Close enough for budget math."""
    content = getattr(message, "content", "") or ""
    if not isinstance(content, str):
        # Fall back to repr so structured tool-call payloads count too.
        content = str(content)
    return len(content) // 4


def compose_working_context(
    messages: list[BaseMessage],
    *,
    max_tokens: int,
    keep_last_n: int,
    summarizer: Summarizer,
) -> tuple[list[BaseMessage], BaseMessage | None]:
    """Return the message list to feed the agent for the next turn.

    Returns ``(kept_messages, optional_summary_message_to_prepend)``.
    The caller is responsible for actually prepending the summary
    (and for persisting it as a ``role='summary'`` row so the next
    turn re-uses it without another Hermes call).

    Raises:
        ValueError: ``max_tokens`` is not strictly positive — guards
            against a misconfigured ``CHAT_HISTORY_MAX_TOKENS`` env
            value (CLAUDE.md §23: fail-fast on bad config).
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be > 0, got {max_tokens}")

    if not messages:
        return [], None

    total = sum(estimate_tokens(m) for m in messages)
    if total <= max_tokens:
        return list(messages), None

    if keep_last_n >= len(messages):
        # Nothing left to summarize; the tail already exceeds the budget,
        # so we let the model truncate. Surfacing this as a structured
        # log line gives operators a signal before it becomes a problem.
        log.warning(
            "chat_history.no_room_to_summarize total=%d max=%d keep_last_n=%d",
            total,
            max_tokens,
            keep_last_n,
        )
        return list(messages), None

    head = messages[:-keep_last_n]
    tail = messages[-keep_last_n:]
    try:
        summary_text = summarizer(head)
    except Exception as exc:
        log.warning(
            "chat_history.summarizer_failed err=%s; falling back to wider tail",
            exc,
        )
        return list(messages[-(2 * keep_last_n) :]), None

    return list(tail), SystemMessage(content=summary_text)