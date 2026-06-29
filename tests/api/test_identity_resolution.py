"""Phase C — Identity dataclass + ``current_identity`` FastAPI dependency.

The lab's existing ``User`` shape (apps/api/middleware/auth.py) is
already populated from a Keycloak JWT by ``AuthMiddleware``. Identity
turns that loose dict-ish thing into a canonical record:

  * ``tenant_id``  — first non-empty Keycloak group, stripped of leading ``/``
  * ``user_id``    — the JWT ``sub`` (User.id)
  * ``role``       — highest-privilege realm role per the policy
                     precedence in ``policies/role_matrix.rego``
  * ``is_admin``   — convenience flag
  * ``is_worker``  — set when the JWT bears the ``worker`` realm role
                     (workers authenticate via Keycloak service account)
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from apps.api.middleware.auth import User


def _req(user: User | None) -> Request:
    """A minimal Request-shaped object whose ``state.user`` we control."""

    class _State:
        pass

    class _R:
        def __init__(self, u: User | None) -> None:
            self.state = _State()
            if u is not None:
                self.state.user = u  # type: ignore[attr-defined]

    return _R(user)  # type: ignore[return-value]


def test_identity_from_admin_user_resolves_tenant_role_user(monkeypatch):
    from apps.api.services.identity import Identity

    u = User(
        id="alice-uuid",
        email="alice@acme",
        roles=["admin", "data_engineer"],
        groups=["/tenants/acme"],
    )
    ident = Identity.from_user(u)
    assert ident.tenant_id == "acme"
    assert ident.user_id == "alice-uuid"
    assert ident.role == "admin"  # admin wins precedence
    assert ident.is_admin is True
    assert ident.is_worker is False


def test_identity_role_precedence_takes_highest():
    from apps.api.services.identity import Identity

    # data_engineer outranks viewer, devops outranks data_engineer, etc.
    u = User(
        id="bob",
        roles=["viewer", "data_engineer", "domain_expert"],
        groups=["/tenants/acme"],
    )
    ident = Identity.from_user(u)
    assert ident.role == "data_engineer"


def test_identity_strips_leading_slash_from_group():
    from apps.api.services.identity import Identity

    u = User(id="c", roles=["viewer"], groups=["/tenants/globex/dept-x"])
    ident = Identity.from_user(u)
    # First group, leading slash stripped, plus the segment after `tenants/`
    assert ident.tenant_id == "globex"


def test_identity_worker_role_marks_is_worker():
    from apps.api.services.identity import Identity

    u = User(id="trainer-bot", roles=["worker"], groups=["/tenants/system"])
    ident = Identity.from_user(u)
    assert ident.is_worker is True
    assert ident.is_admin is False
    assert ident.tenant_id == "system"
    assert ident.role == "worker"


def test_identity_missing_groups_raises():
    """Phase C: auth is mandatory, so a user without a tenant assignment is
    a configuration error — refuse to identify them rather than silently
    falling through to a default tenant."""
    from apps.api.services.identity import Identity

    u = User(id="orphan", roles=["viewer"], groups=[])
    with pytest.raises(ValueError, match="tenant"):
        Identity.from_user(u)


def test_current_identity_dep_returns_identity_for_authed_request():
    from apps.api.services.identity import Identity, current_identity

    u = User(id="alice", roles=["admin"], groups=["/tenants/acme"])
    ident = current_identity(_req(u))
    assert isinstance(ident, Identity)
    assert ident.tenant_id == "acme"


def test_current_identity_dep_raises_401_when_no_user():
    from apps.api.services.identity import current_identity

    with pytest.raises(HTTPException) as ei:
        current_identity(_req(None))
    assert ei.value.status_code == 401
