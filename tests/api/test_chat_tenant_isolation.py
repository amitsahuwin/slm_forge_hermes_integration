"""Cross-tenant isolation on chat conversations.

CLAUDE.md §35: "Data isolation is non-negotiable — no cross-tenant
access." Conversations created under tenant A must be invisible to
tenant B, even with a valid id guess.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.chat import ChatConversation
from apps.api.routers import chat as chat_router
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch):
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.delenv("SLM_FORGE_AUTH_ENABLED", raising=False)
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def _req() -> MagicMock:
    return MagicMock()


def test_create_persists_active_tenant(engine, monkeypatch) -> None:
    """``create_conversation`` reads the tenant from the contextvar and
    stamps it on the row."""
    from packages._log_context import tenant_id_ctx

    tok = tenant_id_ctx.set("acme")
    try:
        with Session(engine) as db:
            chat_router.create_conversation(
                payload=chat_router.ConversationCreate(title="acme-chat"),
                request=_req(),
                db=db,
            )  # type: ignore[call-arg]
        with Session(engine) as db:
            from sqlmodel import select

            row = db.exec(select(ChatConversation)).one()
            assert row.tenant_id == "acme"
            assert row.title == "acme-chat"
    finally:
        tenant_id_ctx.reset(tok)


def test_list_excludes_other_tenants(engine) -> None:
    """Tenant A's list call must NOT include tenant B's conversations."""
    from packages._log_context import tenant_id_ctx

    with Session(engine) as db:
        db.add(ChatConversation(title="A1", tenant_id="acme"))
        db.add(ChatConversation(title="B1", tenant_id="bcorp"))
        db.commit()

    tok = tenant_id_ctx.set("acme")
    try:
        with Session(engine) as db:
            result = chat_router.list_conversations(request=_req(), db=db)  # type: ignore[call-arg]
    finally:
        tenant_id_ctx.reset(tok)
    titles = [c.title for c in result]
    assert "A1" in titles
    assert "B1" not in titles


def test_cross_tenant_read_is_denied(engine) -> None:
    """Tenant B cannot read tenant A's conversation by guessing its id —
    even when auth is *disabled* the tenant boundary is enforced (the
    user's tenant_id contextvar is what drives the check, not OPA)."""
    from packages._log_context import tenant_id_ctx

    with Session(engine) as db:
        c = ChatConversation(title="A-secret", tenant_id="acme", user_id=None)
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id or 0

    tok = tenant_id_ctx.set("bcorp")
    try:
        with Session(engine) as db, pytest.raises(HTTPException) as exc:
            chat_router.list_messages(cid=cid, request=_req(), db=db)  # type: ignore[call-arg]
        assert exc.value.status_code in (403, 404)
    finally:
        tenant_id_ctx.reset(tok)


def test_default_tenant_when_unbound(engine) -> None:
    """When no tenant contextvar is set (worker context), the
    ``default_tenant()`` fallback kicks in — should not crash."""
    from sqlmodel import select

    with Session(engine) as db:
        chat_router.create_conversation(
            payload=chat_router.ConversationCreate(title="t"),
            request=_req(),
            db=db,
        )  # type: ignore[call-arg]
        row = db.exec(select(ChatConversation)).one()
        assert row.tenant_id == "default"