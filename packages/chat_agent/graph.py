"""LangGraph state graph for the SLM-Forge chat agent.

Flow:
    classify_intent ─┬─ tool_call ──► tool_router ──► (ToolNode) ──► assemble_response
                     ├─ chitchat ──► direct_reply
                     └─ clarify  ──► ask_followup

The graph is compiled and exposed via :func:`build_graph`. The companion
:func:`stream_response` is an async generator the API uses to push SSE
events to the browser as the graph executes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from typing import Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from packages.chat_agent.tools import ALL_TOOLS

log = logging.getLogger("chat_agent.graph")

# Default to qwen3:30b-a3b — the model SLM-Forge already requires for the
# Hermes autoresearch loop — so the user doesn't need to pull a second model
# just for the chat UI. Override with CHAT_MODEL env var if a faster model
# is preferred (e.g. qwen2.5:7b for snappier responses).
CHAT_MODEL = os.environ.get(
    "CHAT_MODEL",
    os.environ.get("HERMES_MODEL", "qwen3:30b-a3b"),
)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


SYSTEM_PROMPT = """You are the SLM-Forge co-pilot, embedded in a local fine-tuning lab.

You help the user inspect runs, propose hyperparameters, kick off experiments,
read metrics and check exports. You have tools — USE them whenever the user
asks for live data (runs, datasets, metrics, experiments, exports). Do NOT
fabricate numbers. If the user is just chatting, reply briefly.

When you call a tool, do NOT also write a long prose summary — the UI will
render the tool result as a rich card. Just a one-sentence lead-in is fine.

If a request to mutate state is ambiguous (e.g. "start an experiment" without
a dataset), ask one concise clarifying question instead of guessing.
"""


class ChatState(TypedDict, total=False):
    messages: list[BaseMessage]
    intent: str | None
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    pending_clarification: str | None
    final_response: str | None


# ─── LLM factory ──────────────────────────────────────────────────


def _get_llm(*, with_tools: bool = True) -> Any:
    """Return a ChatOllama instance, optionally bound with tools.

    Falls back to a stub if langchain_ollama can't reach the daemon.
    """
    try:
        from langchain_ollama import ChatOllama

        llm = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_URL, temperature=0.2)
        if with_tools:
            try:
                return llm.bind_tools(ALL_TOOLS)
            except Exception as e:  # noqa: BLE001
                log.warning("bind_tools failed (%s); using LLM without tools", e)
                return llm
        return llm
    except Exception as e:  # noqa: BLE001
        log.warning("ChatOllama unavailable (%s); using fallback echo LLM", e)
        return _EchoLLM()


class _EchoLLM:
    """Fallback when Ollama is offline — keeps the UI responsive."""

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        last_user = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
        )
        text = last_user.content if last_user else ""
        return AIMessage(
            content=(
                f"(Ollama is unreachable at {OLLAMA_URL} — using fallback.) "
                f"You said: {text}"
            )
        )

    def bind_tools(self, _tools: list[Any]) -> "_EchoLLM":
        return self


# ─── Nodes ────────────────────────────────────────────────────────


def classify_intent(state: ChatState) -> ChatState:
    """Lightweight intent classifier.

    We bias toward ``tool_call`` whenever the user's message looks like a data
    request — this is heuristic on purpose: cheaper than an extra LLM hop,
    and the real LLM still has the final say via ``.bind_tools``.
    """
    msgs = state.get("messages", [])
    user = next((m for m in reversed(msgs) if isinstance(m, HumanMessage)), None)
    text = (user.content if user else "").lower().strip()

    tool_keywords = (
        "run", "runs", "dataset", "datasets", "experiment", "experiments",
        "metric", "metrics", "loss", "export", "exports", "start", "kick",
        "propose", "hyperparam", "session", "sessions", "status",
        "docs", "search",
    )
    chitchat_starts = ("hi", "hello", "hey", "thanks", "thank you", "ok", "okay")

    if not text:
        return {**state, "intent": "clarify",
                "pending_clarification": "What would you like to do?"}

    if text.split()[0] in chitchat_starts and len(text) < 40:
        return {**state, "intent": "chitchat"}

    if any(kw in text for kw in tool_keywords):
        return {**state, "intent": "tool_call"}

    # Ambiguously short messages → clarify
    if len(text.split()) < 3:
        return {
            **state,
            "intent": "clarify",
            "pending_clarification": (
                "Could you say a bit more? For example: 'list recent runs' "
                "or 'show metrics for run 42'."
            ),
        }

    return {**state, "intent": "tool_call"}


def tool_router(state: ChatState) -> ChatState:
    """Ask the (tool-bound) LLM to decide which tool(s) to call.

    The LLM's reply is an AIMessage that may contain ``tool_calls``. The next
    node (ToolNode) consumes those and produces ToolMessages.
    """
    llm = _get_llm(with_tools=True)
    msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    msgs.extend(state.get("messages", []))
    reply = llm.invoke(msgs)
    new_messages = list(state.get("messages", [])) + [reply]
    tool_calls = getattr(reply, "tool_calls", None) or []
    return {**state, "messages": new_messages, "tool_calls": list(tool_calls)}


def _after_tool_router(state: ChatState) -> str:
    """If the LLM produced tool_calls, run them; otherwise wrap up."""
    if state.get("tool_calls"):
        return "run_tools"
    return "assemble_response"


def assemble_response(state: ChatState) -> ChatState:
    """Summarize tool results into a final assistant message.

    Tool results are still in the messages list as ToolMessages — we let the
    LLM craft a one-line lead-in. Rich rendering happens UI-side from the
    structured tool outputs we surface via ``tool_results``.
    """
    msgs = list(state.get("messages", []))
    tool_results: list[dict[str, Any]] = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            try:
                content = m.content
                if isinstance(content, str):
                    parsed: Any
                    try:
                        parsed = json.loads(content)
                    except (json.JSONDecodeError, ValueError):
                        parsed = content
                else:
                    parsed = content
                tool_results.append(
                    {
                        "tool": getattr(m, "name", None) or "unknown",
                        "tool_call_id": getattr(m, "tool_call_id", None),
                        "result": parsed,
                    }
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Could not parse ToolMessage: %s", e)

    llm = _get_llm(with_tools=False)
    summary_prompt: list[BaseMessage] = [
        SystemMessage(
            content=(
                "Briefly (1-2 sentences) introduce the tool result(s). The UI "
                "will render the actual data as cards — do not repeat the "
                "data verbatim."
            )
        ),
        *msgs,
    ]
    reply = llm.invoke(summary_prompt)
    final_text = getattr(reply, "content", "") or "Here's what I found."
    return {
        **state,
        "tool_results": tool_results,
        "final_response": final_text,
        "messages": [*msgs, AIMessage(content=final_text)],
    }


def direct_reply(state: ChatState) -> ChatState:
    """Plain chit-chat — single LLM hop, no tools."""
    llm = _get_llm(with_tools=False)
    msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    msgs.extend(state.get("messages", []))
    reply = llm.invoke(msgs)
    text = getattr(reply, "content", "") or ""
    return {
        **state,
        "final_response": text,
        "messages": [*state.get("messages", []), AIMessage(content=text)],
    }


def ask_followup(state: ChatState) -> ChatState:
    """Surface the clarifying question set by classify_intent."""
    question = state.get("pending_clarification") or "Could you clarify?"
    return {
        **state,
        "final_response": question,
        "messages": [*state.get("messages", []), AIMessage(content=question)],
    }


# ─── Graph builder ────────────────────────────────────────────────


def _route_from_classify(state: ChatState) -> str:
    intent = state.get("intent")
    if intent == "tool_call":
        return "tool_router"
    if intent == "chitchat":
        return "direct_reply"
    return "ask_followup"


def build_graph() -> Any:
    """Build & compile the chat agent graph."""
    g = StateGraph(ChatState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("tool_router", tool_router)
    g.add_node("run_tools", ToolNode(ALL_TOOLS))
    g.add_node("assemble_response", assemble_response)
    g.add_node("direct_reply", direct_reply)
    g.add_node("ask_followup", ask_followup)

    g.set_entry_point("classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        _route_from_classify,
        {
            "tool_router": "tool_router",
            "direct_reply": "direct_reply",
            "ask_followup": "ask_followup",
        },
    )
    g.add_conditional_edges(
        "tool_router",
        _after_tool_router,
        {"run_tools": "run_tools", "assemble_response": "assemble_response"},
    )
    g.add_edge("run_tools", "assemble_response")
    g.add_edge("assemble_response", END)
    g.add_edge("direct_reply", END)
    g.add_edge("ask_followup", END)
    return g.compile()


# ─── Streaming helper ─────────────────────────────────────────────


async def stream_response(
    graph: Any,
    history: list[BaseMessage],
    conversation_id: int | str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the graph and emit a sequence of structured events.

    Yields dicts of the shape ``{"type": <kind>, "data": <payload>}``:
      * ``token``       — incremental text (best-effort, per-node summary)
      * ``tool_start``  — a tool is about to be invoked
      * ``tool_end``    — tool finished, with its structured result
      * ``final``       — terminal event with the assistant's reply
    """
    initial: ChatState = {
        "messages": list(history),
        "intent": None,
        "tool_calls": [],
        "tool_results": [],
        "pending_clarification": None,
        "final_response": None,
    }

    seen_tool_ids: set[str] = set()
    final_state: ChatState | None = None

    try:
        async for chunk in graph.astream(initial, stream_mode="values"):
            final_state = chunk
            # Surface in-progress tool calls as soon as they appear
            for call in chunk.get("tool_calls", []) or []:
                cid = call.get("id") or call.get("tool_call_id") or ""
                if cid and cid in seen_tool_ids:
                    continue
                if cid:
                    seen_tool_ids.add(cid)
                yield {
                    "type": "tool_start",
                    "data": {
                        "name": call.get("name"),
                        "args": call.get("args"),
                        "tool_call_id": cid,
                        "ts": time.time(),
                    },
                }
            # Emit tool results once they're attached
            for r in chunk.get("tool_results", []) or []:
                yield {
                    "type": "tool_end",
                    "data": {
                        "name": r.get("tool"),
                        "tool_call_id": r.get("tool_call_id"),
                        "result": r.get("result"),
                        "ts": time.time(),
                    },
                }
    except Exception as e:  # noqa: BLE001
        log.exception("Graph stream failed")
        yield {"type": "final", "data": {"error": str(e), "conversation_id": conversation_id}}
        return

    text = (final_state or {}).get("final_response") or ""
    yield {
        "type": "token",
        "data": {"text": text, "conversation_id": conversation_id},
    }
    yield {
        "type": "final",
        "data": {
            "text": text,
            "conversation_id": conversation_id,
            "tool_results": (final_state or {}).get("tool_results", []),
        },
    }
