"""Chat router — conversation CRUD + LangGraph-backed SSE stream."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.middleware.auth import requires
from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.services.db import get_session
from apps.api.services.tenant import current_tenant

log = logging.getLogger("api.chat")
router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


# ─── Pre-flight health ────────────────────────────────────────────


class ChatHealth(BaseModel):
    imports_ok: bool
    imports_error: str | None
    ollama_reachable: bool
    chat_model: str
    model_available: bool
    ollama_url: str
    hint: str | None


def _check_chat_health() -> ChatHealth:
    """Verify everything the chat graph needs is in place."""
    imports_ok = True
    imports_error: str | None = None
    try:
        # These are the two heavy imports that fail when the chat extra isn't
        # installed in the API container. Importing here surfaces the real
        # exception instead of dying inside the SSE generator.
        import langchain_ollama  # noqa: F401
        import langgraph  # noqa: F401

        from packages.chat_agent.graph import CHAT_MODEL, OLLAMA_URL  # noqa: F401
    except Exception as e:
        imports_ok = False
        imports_error = f"{type(e).__name__}: {e}"

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    chat_model = os.environ.get(
        "CHAT_MODEL", os.environ.get("HERMES_MODEL", "qwen3:30b-a3b")
    )

    ollama_reachable = False
    model_available = False
    hint: str | None = None
    if imports_ok:
        try:
            r = httpx.get(f"{ollama_url}/api/version", timeout=2)
            ollama_reachable = r.status_code == 200
        except Exception:
            ollama_reachable = False

        if ollama_reachable:
            try:
                r = httpx.post(
                    f"{ollama_url}/api/show",
                    json={"name": chat_model},
                    timeout=5,
                )
                model_available = r.status_code == 200
            except Exception:
                model_available = False

    if not imports_ok:
        hint = (
            "LangGraph/LangChain not installed in the API. Rebuild with "
            "`docker compose up -d --build` (the Dockerfile now installs the "
            "`chat` extra) or run `uv sync --extra chat` if running outside Docker."
        )
    elif not ollama_reachable:
        hint = (
            f"Ollama is not reachable at {ollama_url}. From the host: "
            "`brew services restart ollama` and verify with `ollama list`."
        )
    elif not model_available:
        hint = (
            f"Chat model '{chat_model}' is not pulled in Ollama. Run "
            f"`ollama pull {chat_model}` — or set CHAT_MODEL in .env to a "
            "model you already have."
        )

    return ChatHealth(
        imports_ok=imports_ok,
        imports_error=imports_error,
        ollama_reachable=ollama_reachable,
        chat_model=chat_model,
        model_available=model_available,
        ollama_url=ollama_url,
        hint=hint,
    )


@router.get("/health", response_model=ChatHealth)
def chat_health() -> ChatHealth:
    """Return everything that has to be working for the chat UI to function.

    The frontend calls this on mount and surfaces ``hint`` as a banner if any
    component is unhealthy — that way users see "pull this model" instead of
    a generic "stream interrupted".
    """
    return _check_chat_health()


# ─── Schemas ──────────────────────────────────────────────────────


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: str


class MessageCreate(BaseModel):
    content: str


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    created_at: str


# ─── Helpers ──────────────────────────────────────────────────────


def _serialize_conversation(c: ChatConversation) -> ConversationOut:
    return ConversationOut(
        id=c.id or 0, title=c.title, created_at=c.created_at.isoformat()
    )


def _serialize_message(m: ChatMessage) -> MessageOut:
    tcs: list[dict[str, Any]] | None = None
    if m.tool_calls_json:
        try:
            parsed = json.loads(m.tool_calls_json)
            if isinstance(parsed, list):
                tcs = parsed
        except json.JSONDecodeError:
            tcs = None
    return MessageOut(
        id=m.id or 0,
        conversation_id=m.conversation_id,
        role=m.role,
        content=m.content,
        tool_calls=tcs,
        created_at=m.created_at.isoformat(),
    )


# ─── Conversation endpoints ───────────────────────────────────────


def _active_user_id(request: Request) -> str | None:
    """Best-effort user id from ``request.state.user``. ``None`` in disabled
    mode means "no per-user binding" — the conversation will carry NULL
    user_id and be tenant-scoped only (per the spec)."""
    user = getattr(getattr(request, "state", None), "user", None)
    if user is None:
        return None
    uid = getattr(user, "id", None)
    return str(uid) if uid else None


def _is_admin(request: Request) -> bool:
    user = getattr(getattr(request, "state", None), "user", None)
    roles = getattr(user, "roles", None) or []
    return "admin" in roles


def _load_convo_or_403(db: Session, cid: int, request: Request) -> ChatConversation:
    """Fetch the conversation and enforce tenant + owner access.

    Hard rules:
      * 404 if no row.
      * 403 if the row's tenant differs from the active tenant.
      * 403 if a user_id is set AND it doesn't match the active user AND
        the active user is not admin.
    """
    convo = db.get(ChatConversation, cid)
    if not convo:
        raise HTTPException(404, "Conversation not found")
    if convo.tenant_id != current_tenant():
        raise HTTPException(403, "Conversation belongs to a different tenant")
    if convo.user_id is not None:
        active_uid = _active_user_id(request)
        if not _is_admin(request) and active_uid is not None and active_uid != convo.user_id:
            raise HTTPException(403, "You don't own this conversation")
    return convo


@router.post("/conversations", response_model=ConversationOut)
@requires("create", "chat")
def create_conversation(
    payload: ConversationCreate, request: Request, db: SessionDep
) -> ConversationOut:
    # Spec §R3: when auth is disabled the user_id stays NULL (tenant-only
    # scoping); when enabled, we stamp the active user's id so the
    # ownership check has something to compare against.
    from apps.api.services import auth_settings as auth_settings_module

    uid: str | None = None
    if auth_settings_module.get_auth_settings().auth_enabled:
        uid = _active_user_id(request)
    c = ChatConversation(
        title=payload.title or "New conversation",
        tenant_id=current_tenant(),
        user_id=uid,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize_conversation(c)


@router.get("/conversations", response_model=list[ConversationOut])
@requires("read", "chat")
def list_conversations(
    request: Request, db: SessionDep, limit: int = 50
) -> list[ConversationOut]:
    tenant = current_tenant()
    stmt = (
        select(ChatConversation)
        .where(ChatConversation.tenant_id == tenant)
        .order_by(desc(ChatConversation.created_at))  # type: ignore[arg-type]
        .limit(limit)
    )
    rows = db.exec(stmt).all()
    # Row-level ownership: non-admins only see their own (and ownerless)
    # conversations within the tenant.
    if not _is_admin(request):
        uid = _active_user_id(request)
        rows = [c for c in rows if c.user_id is None or c.user_id == uid]
    return [_serialize_conversation(c) for c in rows]


@router.get(
    "/conversations/{cid}/messages",
    response_model=list[MessageOut],
)
@requires("read", "chat")
def list_messages(
    cid: int, request: Request, db: SessionDep
) -> list[MessageOut]:
    _load_convo_or_403(db, cid, request)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == cid)
        .order_by(ChatMessage.id)  # type: ignore[arg-type]
    )
    return [_serialize_message(m) for m in db.exec(stmt).all()]


@router.post(
    "/conversations/{cid}/messages",
    response_model=MessageOut,
)
@requires("create", "chat")
def post_message(
    cid: int, payload: MessageCreate, request: Request, db: SessionDep
) -> MessageOut:
    convo = _load_convo_or_403(db, cid, request)
    msg = ChatMessage(
        conversation_id=cid,
        role="user",
        content=payload.content,
        tenant_id=convo.tenant_id,
    )
    db.add(msg)

    # Auto-title the conversation from the first user message
    if convo.title == "New conversation" and payload.content.strip():
        convo.title = payload.content.strip()[:60]
        db.add(convo)

    db.commit()
    db.refresh(msg)
    return _serialize_message(msg)


def _apply_history_budget(
    history: list[Any],
) -> tuple[list[Any], Any | None]:
    """Apply the configured token budget to a loaded history.

    Reads ``CHAT_HISTORY_MAX_TOKENS`` (default 24576 — 75% of qwen3:30b-a3b's
    32k window) and ``CHAT_HISTORY_KEEP_LAST_N`` (default 8). Delegates to
    ``packages.chat_agent.history.compose_working_context``, which is pure
    and unit-tested. The summarizer is the Hermes
    ``summarize_chat_window`` skill — one in-band call that's free
    when the budget is not exceeded.

    Returns ``(working_messages, optional_summary_to_persist)``. The
    caller is responsible for persisting the summary as a
    ``role='summary'`` row so the next turn re-uses it.
    """
    import os

    from packages.chat_agent.history import compose_working_context

    try:
        max_tokens = int(os.environ.get("CHAT_HISTORY_MAX_TOKENS", "24576"))
    except ValueError as e:
        raise RuntimeError(
            "Invalid CHAT_HISTORY_MAX_TOKENS — must be a positive integer"
        ) from e
    try:
        keep_last_n = int(os.environ.get("CHAT_HISTORY_KEEP_LAST_N", "8"))
    except ValueError as e:
        raise RuntimeError(
            "Invalid CHAT_HISTORY_KEEP_LAST_N — must be a positive integer"
        ) from e

    def _summarize(head: list[Any]) -> str:
        """Hermes-backed summarizer. Returns plain text per the skill spec."""
        from packages.ratchet.hermes_bridge import run_skill

        payload: dict[str, Any] = {
            "head": [
                {
                    "role": _role_for_summary(m),
                    "content": getattr(m, "content", ""),
                }
                for m in head
            ],
            "previous_summary": None,
        }
        return run_skill("summarize_chat_window", payload, expect_json=False)

    return compose_working_context(
        history,
        max_tokens=max_tokens,
        keep_last_n=keep_last_n,
        summarizer=_summarize,
    )


def _role_for_summary(message: Any) -> str:
    """Map a LangChain BaseMessage subtype back to a string role label
    so the summarize-chat-window skill input is human-readable JSON."""
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, SystemMessage):
        return "system"
    return "unknown"


def _persist_turn(
    db: Session,
    cid: int,
    *,
    final_text: str,
    tool_results: list[dict[str, Any]],
) -> None:
    """Persist one completed chat turn as discrete rows so the next turn's
    ``_load_history_as_langchain`` can rebuild the LangChain sequence.

    Row sequence when tools fired:
      * ``role='assistant'`` content="" tool_calls_json=[{id,name,args}]
      * one ``role='tool'`` per result, content=result_json,
        tool_calls_json={"tool_call_id":..., "name":...}
      * ``role='assistant'`` content=final_text tool_calls_json=NULL

    Row sequence when no tools fired:
      * ``role='assistant'`` content=final_text tool_calls_json=NULL
    """
    if tool_results:
        tool_calls_payload: list[dict[str, Any]] = []
        for r in tool_results:
            name = r.get("tool") or r.get("name")
            tcid = r.get("tool_call_id")
            if not tcid:
                # Skip results lacking a tool_call_id — they can't be
                # round-tripped to a ToolMessage on the next turn.
                continue
            tool_calls_payload.append({"id": tcid, "name": name, "args": r.get("args") or {}})
        if tool_calls_payload:
            db.add(
                ChatMessage(
                    conversation_id=cid,
                    role="assistant",
                    content="",
                    tool_calls_json=json.dumps(tool_calls_payload),
                )
            )
            for r in tool_results:
                tcid = r.get("tool_call_id")
                if not tcid:
                    continue
                result_text = r.get("result")
                content = (
                    json.dumps(result_text)
                    if not isinstance(result_text, str)
                    else result_text
                )
                db.add(
                    ChatMessage(
                        conversation_id=cid,
                        role="tool",
                        content=content,
                        tool_calls_json=json.dumps(
                            {"tool_call_id": tcid, "name": r.get("tool")}
                        ),
                    )
                )

    db.add(
        ChatMessage(
            conversation_id=cid,
            role="assistant",
            content=final_text,
            tool_calls_json=None,
        )
    )
    db.commit()


def _record_chat_turn_trace(
    *,
    conversation_id: int,
    message_count_in: int,
    summarization_fired: bool,
    final_text: str,
    tool_result_count: int,
    duration_ms: int,
    error: str | None,
) -> None:
    """One ``HermesTrace`` row per chat turn.

    Reuses the existing ``hermes_traces`` table so chat activity shows up
    alongside Hermes skill calls in the Skill-Activity view — no new
    table, no new UI. Tenant + user correlation come from the contextvars
    that ``_record_trace`` already reads.
    """
    try:
        from sqlmodel import Session as _Session

        from apps.api.models.chat import ChatConversation as _Convo
        from apps.api.models.hermes_trace import HermesTrace
        from apps.api.services.db import engine as _engine

        with _Session(_engine) as db:
            convo = db.get(_Convo, conversation_id)
            tenant_id = convo.tenant_id if convo else "default"
            db.add(
                HermesTrace(
                    source="chat:turn",
                    model="chat-graph",
                    request_body=json.dumps(
                        {
                            "conversation_id": conversation_id,
                            "message_count_in": message_count_in,
                            "summarization_fired": summarization_fired,
                        }
                    ),
                    response_body=json.dumps(
                        {
                            "final_text_chars": len(final_text or ""),
                            "tool_result_count": tool_result_count,
                        }
                    ),
                    error=error,
                    duration_ms=duration_ms,
                    attempts=1,
                    tenant_id=tenant_id,
                    skill_name="chat:turn",
                    success=error is None,
                )
            )
            db.commit()
    except Exception as e:
        log.warning("Could not record chat:turn trace: %s", e)


# ─── SSE stream ──────────────────────────────────────────────────


def _load_history_as_langchain(
    db: Session, cid: int
) -> list[Any]:  # returns list[BaseMessage] at runtime
    """Convert persisted chat messages into LangChain BaseMessages.

    Replays *all* relevant roles in chronological order so the agent sees
    the full context of prior turns — including tool calls and tool
    results. Without this fidelity, the agent forgets across turns which
    tools it has already run.

    Role handling:
      * ``user``      → ``HumanMessage``
      * ``assistant`` → ``AIMessage`` (carrying parsed ``tool_calls_json``
                        as ``tool_calls=...`` so the agent doesn't re-fire
                        already-completed calls)
      * ``tool``      → ``ToolMessage`` (``tool_call_id`` extracted from
                        ``tool_calls_json``; empty string when the source
                        row didn't record one)
      * ``summary``   → ``SystemMessage`` — only the *most recent* summary
                        row is kept so the agent never sees stale or
                        duplicated summaries
      * any other     → skipped (forward-compatible)
    """
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == cid)
        .order_by(ChatMessage.id)  # type: ignore[arg-type]
    )
    rows = list(db.exec(stmt).all())

    # Find the latest summary row (if any) and drop earlier ones.
    latest_summary_idx = -1
    for i, m in enumerate(rows):
        if m.role == "summary":
            latest_summary_idx = i
    if latest_summary_idx >= 0:
        latest = rows[latest_summary_idx]
        rows = [m for m in rows if m.role != "summary"]
        # Prepend the latest summary as the head SystemMessage.
        rows.insert(0, latest)

    out: list[Any] = []
    for m in rows:
        if m.role == "summary":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            tcs: list[dict[str, Any]] = []
            if m.tool_calls_json:
                try:
                    parsed = json.loads(m.tool_calls_json)
                    if isinstance(parsed, list):
                        tcs = parsed
                except json.JSONDecodeError:
                    tcs = []
            if tcs:
                out.append(AIMessage(content=m.content, tool_calls=tcs))
            else:
                out.append(AIMessage(content=m.content))
        elif m.role == "tool":
            tool_call_id = ""
            if m.tool_calls_json:
                try:
                    parsed_tc = json.loads(m.tool_calls_json)
                    if isinstance(parsed_tc, dict):
                        tool_call_id = str(parsed_tc.get("tool_call_id") or "")
                except json.JSONDecodeError:
                    tool_call_id = ""
            out.append(ToolMessage(content=m.content, tool_call_id=tool_call_id))
        # Unknown roles (forward-compat) are silently skipped.
    return out


@router.get("/conversations/{cid}/stream")
async def stream_conversation(cid: int) -> EventSourceResponse:
    """Run the LangGraph agent against the conversation history & stream events.

    Pre-flights health first. If the env isn't viable (deps missing, Ollama
    down, model not pulled) we emit a single structured ``error`` event with
    a human-readable hint and close cleanly — the UI renders this as a banner
    rather than a generic "stream interrupted".
    """
    from sqlmodel import Session as _Session

    from apps.api.services.db import engine

    # 1. Pre-flight before importing the graph (graph import triggers langgraph).
    health = _check_chat_health()
    if not (health.imports_ok and health.ollama_reachable and health.model_available):
        msg = health.hint or "Chat backend is not ready."

        async def error_gen() -> AsyncGenerator[dict[str, str], None]:
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": msg,
                        "imports_ok": health.imports_ok,
                        "imports_error": health.imports_error,
                        "ollama_reachable": health.ollama_reachable,
                        "chat_model": health.chat_model,
                        "model_available": health.model_available,
                    }
                ),
            }
            yield {
                "event": "done",
                "data": json.dumps({"conversation_id": cid, "reason": "pre-flight"}),
            }

        return EventSourceResponse(error_gen())

    # 2. Validate the conversation exists, snapshot history, apply the
    #    token-budget guard. If the working context would overflow the
    #    model's window, we summarize the older head and persist the
    #    summary as a ``role='summary'`` row so the next turn re-uses
    #    it without another Hermes call.
    with _Session(engine) as db:
        if not db.get(ChatConversation, cid):
            raise HTTPException(404, "Conversation not found")
        history = _load_history_as_langchain(db, cid)
        history, summary_to_persist = _apply_history_budget(history)
        summarization_fired = summary_to_persist is not None
        if summary_to_persist is not None:
            db.add(
                ChatMessage(
                    conversation_id=cid,
                    role="summary",
                    content=getattr(summary_to_persist, "content", ""),
                    tool_calls_json=None,
                )
            )
            db.commit()
    message_count_in = len(history)

    # 3. Import + build the graph; if this fails (rare after pre-flight) we
    # still surface a usable error.
    try:
        from packages.chat_agent.graph import build_graph, stream_response

        graph = build_graph()
    except Exception as e:
        log.exception("graph build failed")
        build_err_msg = f"Failed to build chat graph: {e}"

        async def build_error_gen() -> AsyncGenerator[dict[str, str], None]:
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": build_err_msg,
                        "stage": "build_graph",
                    }
                ),
            }
            yield {
                "event": "done",
                "data": json.dumps({"conversation_id": cid, "reason": "build-error"}),
            }

        return EventSourceResponse(build_error_gen())

    async def event_gen() -> AsyncGenerator[dict[str, str], None]:
        import time as _time

        final_text = ""
        final_tool_results: list[dict[str, Any]] = []
        start = _time.monotonic()
        try:
            async for ev in stream_response(graph, history, cid):
                yield {"event": ev["type"], "data": json.dumps(ev["data"])}
                if ev["type"] == "final":
                    final_text = ev["data"].get("text", "") or final_text
                    final_tool_results = ev["data"].get("tool_results", []) or []
        except Exception as e:
            log.exception("chat stream failed mid-run")
            duration_ms = int((_time.monotonic() - start) * 1000)
            _record_chat_turn_trace(
                conversation_id=cid,
                message_count_in=message_count_in,
                summarization_fired=summarization_fired,
                final_text="",
                tool_result_count=0,
                duration_ms=duration_ms,
                error=f"{type(e).__name__}: {e}",
            )
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": f"{type(e).__name__}: {e}",
                        "stage": "stream",
                    }
                ),
            }
            yield {
                "event": "done",
                "data": json.dumps({"conversation_id": cid, "reason": "stream-error"}),
            }
            return

        # Persist the assistant turn — split tool calls + tool results into
        # discrete rows so the next turn's history replay reconstructs the
        # full LangChain message sequence (AIMessage with tool_calls,
        # ToolMessage(s), final AIMessage).
        try:
            with _Session(engine) as db:
                _persist_turn(
                    db,
                    cid,
                    final_text=final_text or "",
                    tool_results=final_tool_results,
                )
        except Exception as e:
            log.warning("Could not persist assistant message: %s", e)

        duration_ms = int((_time.monotonic() - start) * 1000)
        _record_chat_turn_trace(
            conversation_id=cid,
            message_count_in=message_count_in,
            summarization_fired=summarization_fired,
            final_text=final_text or "",
            tool_result_count=len(final_tool_results),
            duration_ms=duration_ms,
            error=None,
        )
        yield {"event": "done", "data": json.dumps({"conversation_id": cid})}

    return EventSourceResponse(event_gen())
