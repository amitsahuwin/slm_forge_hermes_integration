"""Chat router — conversation CRUD + LangGraph-backed SSE stream."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.services.db import get_session

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
        import langgraph  # noqa: F401
        import langchain_ollama  # noqa: F401

        from packages.chat_agent.graph import CHAT_MODEL, OLLAMA_URL  # noqa: F401
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            ollama_reachable = False

        if ollama_reachable:
            try:
                r = httpx.post(
                    f"{ollama_url}/api/show",
                    json={"name": chat_model},
                    timeout=5,
                )
                model_available = r.status_code == 200
            except Exception:  # noqa: BLE001
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


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(payload: ConversationCreate, db: SessionDep) -> ConversationOut:
    c = ChatConversation(title=payload.title or "New conversation")
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize_conversation(c)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: SessionDep, limit: int = 50) -> list[ConversationOut]:
    stmt = (
        select(ChatConversation)
        .order_by(desc(ChatConversation.created_at))
        .limit(limit)
    )
    return [_serialize_conversation(c) for c in db.exec(stmt).all()]


@router.get(
    "/conversations/{cid}/messages",
    response_model=list[MessageOut],
)
def list_messages(cid: int, db: SessionDep) -> list[MessageOut]:
    if not db.get(ChatConversation, cid):
        raise HTTPException(404, "Conversation not found")
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == cid)
        .order_by(ChatMessage.id)
    )
    return [_serialize_message(m) for m in db.exec(stmt).all()]


@router.post(
    "/conversations/{cid}/messages",
    response_model=MessageOut,
)
def post_message(cid: int, payload: MessageCreate, db: SessionDep) -> MessageOut:
    if not db.get(ChatConversation, cid):
        raise HTTPException(404, "Conversation not found")
    msg = ChatMessage(conversation_id=cid, role="user", content=payload.content)
    db.add(msg)

    # Auto-title the conversation from the first user message
    convo = db.get(ChatConversation, cid)
    if convo and convo.title == "New conversation" and payload.content.strip():
        convo.title = payload.content.strip()[:60]
        db.add(convo)

    db.commit()
    db.refresh(msg)
    return _serialize_message(msg)


# ─── SSE stream ──────────────────────────────────────────────────


def _load_history_as_langchain(
    db: Session, cid: int
) -> list[Any]:  # returns list[BaseMessage] at runtime
    """Convert persisted chat messages into LangChain BaseMessages."""
    from langchain_core.messages import AIMessage, HumanMessage

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == cid)
        .order_by(ChatMessage.id)
    )
    out: list[Any] = []
    for m in db.exec(stmt).all():
        if m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            out.append(AIMessage(content=m.content))
        # tool/system messages are not replayed
    return out


@router.get("/conversations/{cid}/stream")
async def stream_conversation(cid: int) -> EventSourceResponse:
    """Run the LangGraph agent against the conversation history & stream events.

    Pre-flights health first. If the env isn't viable (deps missing, Ollama
    down, model not pulled) we emit a single structured ``error`` event with
    a human-readable hint and close cleanly — the UI renders this as a banner
    rather than a generic "stream interrupted".
    """
    from apps.api.services.db import engine
    from sqlmodel import Session as _Session

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

    # 2. Validate the conversation exists and snapshot history.
    with _Session(engine) as db:
        if not db.get(ChatConversation, cid):
            raise HTTPException(404, "Conversation not found")
        history = _load_history_as_langchain(db, cid)

    # 3. Import + build the graph; if this fails (rare after pre-flight) we
    # still surface a usable error.
    try:
        from packages.chat_agent.graph import build_graph, stream_response

        graph = build_graph()
    except Exception as e:  # noqa: BLE001
        log.exception("graph build failed")

        async def build_error_gen() -> AsyncGenerator[dict[str, str], None]:
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": f"Failed to build chat graph: {e}",
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
        final_text = ""
        final_tool_results: list[dict[str, Any]] = []
        try:
            async for ev in stream_response(graph, history, cid):
                yield {"event": ev["type"], "data": json.dumps(ev["data"])}
                if ev["type"] == "final":
                    final_text = ev["data"].get("text", "") or final_text
                    final_tool_results = ev["data"].get("tool_results", []) or []
        except Exception as e:  # noqa: BLE001
            log.exception("chat stream failed mid-run")
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

        # Persist the assistant turn
        try:
            with _Session(engine) as db:
                assistant_msg = ChatMessage(
                    conversation_id=cid,
                    role="assistant",
                    content=final_text or "",
                    tool_calls_json=(
                        json.dumps(final_tool_results) if final_tool_results else None
                    ),
                )
                db.add(assistant_msg)
                db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("Could not persist assistant message: %s", e)

        yield {"event": "done", "data": json.dumps({"conversation_id": cid})}

    return EventSourceResponse(event_gen())
