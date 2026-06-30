"""OPA gate on the new traces endpoints.

The Traces tab is admin-only by policy because every row carries
request bodies (prompts, dataset rows, model metadata). The existing
``GET /traces`` and ``DELETE /traces`` already enforce
``@requires("read", "setting")`` / ``@requires("delete", "setting")``.
The new filter parameters and ``/skills/summary`` endpoint must
inherit the same gate — no policy bypass is allowed.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.middleware import auth as auth_module
from apps.api.middleware.auth import User
from apps.api.models.hermes_trace import HermesTrace
from apps.api.routers.traces import list_skill_summary, list_traces
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture()
def auth_enabled(monkeypatch: pytest.MonkeyPatch):
    """Flip enforcement on. ``get_auth_settings`` is ``lru_cache``d, so we
    clear it before and after."""
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.setenv("SLM_FORGE_AUTH_ENABLED", "true")
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def deny_all_policy(monkeypatch: pytest.MonkeyPatch):
    """All OPA decisions land as deny → the @requires gate raises 403."""
    monkeypatch.setattr(
        auth_module, "policy_check", lambda user, action, resource, settings=None: (False, "denied")
    )


@pytest.fixture()
def allow_all_policy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_module, "policy_check", lambda user, action, resource, settings=None: (True, "")
    )


@pytest.fixture()
def empty_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def _req_with_user(user: User | None) -> MagicMock:
    """A Request-shaped mock carrying ``request.state.user``.

    Phase D — the user must carry a ``/tenants/<name>`` group for
    ``current_identity()`` to resolve, otherwise it raises 403. Tests
    that pass a user without groups get a tenant=default fallback so
    the seed rows (also tenant_id='default') are visible."""
    if user is not None and not (user.groups or []):
        user = User(
            id=user.id, email=user.email, roles=user.roles,
            groups=["/tenants/default"],
        )
    req = MagicMock()
    req.state.user = user
    return req


def test_list_traces_denied_for_non_admin(auth_enabled, deny_all_policy, empty_engine):
    user = User(id="dev1", email="dev@x", roles=["dev"])
    with Session(empty_engine) as db, pytest.raises(HTTPException) as exc:
        list_traces(request=_req_with_user(user), db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 403


def test_skill_summary_denied_for_non_admin(auth_enabled, deny_all_policy, empty_engine):
    user = User(id="dev1", email="dev@x", roles=["dev"])
    with Session(empty_engine) as db, pytest.raises(HTTPException) as exc:
        list_skill_summary(request=_req_with_user(user), db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 403


def test_unauthenticated_request_rejected(auth_enabled, allow_all_policy, empty_engine):
    """When auth is on and ``request.state.user`` is unset, the gate must
    reject with 401 — never silently treat the caller as anonymous."""
    req = MagicMock()
    req.state.user = None
    with Session(empty_engine) as db, pytest.raises(HTTPException) as exc:
        list_skill_summary(request=req, db=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 401


def test_admin_user_allowed(auth_enabled, allow_all_policy, empty_engine):
    """Sanity: with auth on and policy allowing, the endpoint returns."""
    admin = User(id="admin", email="admin@local", roles=["admin"])
    with Session(empty_engine) as db:
        result = list_skill_summary(request=_req_with_user(admin), db=db)  # type: ignore[call-arg]
    assert result == []  # empty engine, but call succeeded


def test_auth_disabled_is_passthrough(empty_engine, monkeypatch):
    """Default dev workflow: auth off → no gate, no rejection. This is the
    existing behavior; it must keep working."""
    monkeypatch.delenv("SLM_FORGE_AUTH_ENABLED", raising=False)
    auth_settings_module.get_auth_settings.cache_clear()
    with Session(empty_engine) as db:
        # No user attached, no policy mocked — must just return.
        # Phase D — explicitly null out state.user so current_identity()
        # falls back to the synthetic admin (auth-disabled passthrough).
        req = MagicMock()
        req.state.user = None
        result = list_skill_summary(request=req, db=db)  # type: ignore[call-arg]
    assert result == []
