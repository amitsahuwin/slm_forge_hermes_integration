"""Chat endpoints must enforce OPA + per-conversation ownership.

Pre-spec the chat router had no ``@requires`` gate — anyone with a
conversation id could read or post to it. CLAUDE.md §19 (AAA) and §35
(tenant isolation) both flag this.

After this change every endpoint runs the existing OPA gate (the role
matrix already has ``chat`` as a resource) AND a row-level ownership
check: the active user must be admin, or the conversation owner, or
the conversation must be ownerless within their tenant.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.middleware import auth as auth_module
from apps.api.middleware.auth import User
from apps.api.models.chat import ChatConversation
from apps.api.routers import chat as chat_router
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture()
def auth_enabled(monkeypatch: pytest.MonkeyPatch):
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.setenv("SLM_FORGE_AUTH_ENABLED", "true")
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def deny_all_policy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (False, "denied"),
    )


@pytest.fixture()
def allow_all_policy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (True, ""),
    )


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def _req(user: User | None) -> MagicMock:
    req = MagicMock()
    req.state.user = user
    return req


# ---------------------------------------------------------------------------
# Policy gate (OPA via @requires)
# ---------------------------------------------------------------------------


def test_list_conversations_denied_for_no_chat_role(
    auth_enabled, deny_all_policy, isolated_engine
):
    user = User(id="nobody", email="nobody@x", roles=["unknown_role"])
    with Session(isolated_engine) as db, pytest.raises(HTTPException) as exc:
        chat_router.list_conversations(request=_req(user), db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 403


def test_post_message_denied_when_policy_denies(
    auth_enabled, deny_all_policy, isolated_engine
):
    """Even with a valid user, a role lacking chat:create gets 403."""
    user = User(id="dev1", email="dev@x", roles=["support"])  # read-only
    with Session(isolated_engine) as db:
        c = ChatConversation(title="t", tenant_id="default", user_id="dev1")
        db.add(c)
        db.commit()
        db.refresh(c)
        with pytest.raises(HTTPException) as exc:
            chat_router.post_message(
                cid=c.id or 0,
                payload=chat_router.MessageCreate(content="hi"),
                request=_req(user),
                db=db,
            )  # type: ignore[call-arg]
    assert exc.value.status_code == 403


def test_unauthenticated_request_rejected(auth_enabled, allow_all_policy, isolated_engine):
    """When auth is on and ``request.state.user`` is unset, the gate must 401."""
    with Session(isolated_engine) as db, pytest.raises(HTTPException) as exc:
        chat_router.list_conversations(request=_req(None), db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Row-level ownership check
# ---------------------------------------------------------------------------


def test_user_cannot_read_other_users_conversation(
    auth_enabled, allow_all_policy, isolated_engine
):
    """OPA may allow the *action* but the row's ``user_id`` still gates
    access. Otherwise any signed-in user could read anyone else's chat
    by guessing an id."""
    with Session(isolated_engine) as db:
        c = ChatConversation(title="alice's chat", tenant_id="default", user_id="alice")
        db.add(c)
        db.commit()
        db.refresh(c)
        bob = User(id="bob", email="bob@x", roles=["data_engineer"])
        with pytest.raises(HTTPException) as exc:
            chat_router.list_messages(cid=c.id or 0, request=_req(bob), db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 403


def test_admin_can_read_any_users_conversation(
    auth_enabled, allow_all_policy, isolated_engine
):
    """Admins bypass row-level ownership — they manage the system."""
    with Session(isolated_engine) as db:
        c = ChatConversation(title="alice's chat", tenant_id="default", user_id="alice")
        db.add(c)
        db.commit()
        db.refresh(c)
        admin = User(id="root", email="root@x", roles=["admin"])
        # Should not raise.
        msgs = chat_router.list_messages(cid=c.id or 0, request=_req(admin), db=db)  # type: ignore[call-arg]
        assert msgs == []


def test_owner_can_read_their_own_conversation(
    auth_enabled, allow_all_policy, isolated_engine
):
    with Session(isolated_engine) as db:
        c = ChatConversation(title="alice's chat", tenant_id="default", user_id="alice")
        db.add(c)
        db.commit()
        db.refresh(c)
        alice = User(id="alice", email="alice@x", roles=["data_engineer"])
        msgs = chat_router.list_messages(cid=c.id or 0, request=_req(alice), db=db)  # type: ignore[call-arg]
        assert msgs == []


def test_auth_disabled_is_passthrough(isolated_engine, monkeypatch) -> None:
    """Default dev mode: no gate, no rejection."""
    monkeypatch.delenv("SLM_FORGE_AUTH_ENABLED", raising=False)
    auth_settings_module.get_auth_settings.cache_clear()
    with Session(isolated_engine) as db:
        result = chat_router.list_conversations(request=MagicMock(), db=db)  # type: ignore[call-arg]
    assert result == []