"""Chat router — conversation CRUD + LangGraph-backed SSE stream."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.models.chat import ChatConversation, ChatMessage
from apps.api.services.db import get_session

log = logging.getLogger("api.chat")
router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


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
    """Run the LangGraph agent against the conversation history & stream events."""
    from apps.api.services.db import engine
    from sqlmodel import Session as _Session

    from packages.chat_agent.graph import build_graph, stream_response

    # Build state up front using a short-lived session
    with _Session(engine) as db:
        if not db.get(ChatConversation, cid):
            raise HTTPException(404, "Conversation not found")
        history = _load_history_as_langchain(db, cid)

    graph = build_graph()

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
            log.exception("chat stream failed")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
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
