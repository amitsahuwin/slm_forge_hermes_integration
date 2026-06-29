"""Phase C — when a worker presents a Keycloak ``client_credentials`` JWT,
the middleware promotes it into a worker identity:

  * ``role=worker``  (added if absent)
  * ``groups=["/tenants/system"]``  (added if absent)

so ``Identity.from_user`` resolves to ``tenant_id=system, role=worker``.
The promotion is gated on the JWT's ``azp`` (authorized party) claim
matching ``SLM_FORGE_WORKER_CLIENT_ID``.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.api.middleware.auth import _build_user_from_claims
from apps.api.services.identity import Identity


def _claims(*, azp: str, roles: list[str] | None = None, groups: list[str] | None = None) -> dict:
    return {
        "sub": "service-account-slm-forge-worker",
        "preferred_username": "service-account-slm-forge-worker",
        "azp": azp,
        "realm_access": {"roles": roles or []},
        "groups": groups or [],
    }


@pytest.fixture(autouse=True)
def _wid(monkeypatch):
    monkeypatch.setenv("SLM_FORGE_WORKER_CLIENT_ID", "slm-forge-worker")


def test_worker_jwt_promotes_to_worker_role_and_system_tenant(monkeypatch):
    user = _build_user_from_claims(_claims(azp="slm-forge-worker"))
    assert "worker" in user.roles
    assert "/tenants/system" in user.groups
    ident = Identity.from_user(user)
    assert ident.tenant_id == "system"
    assert ident.role == "worker"
    assert ident.is_worker is True
    assert ident.is_admin is False


def test_human_jwt_is_not_promoted(monkeypatch):
    # azp differs (e.g. the web client) — no promotion.
    user = _build_user_from_claims(
        _claims(azp="slm-forge-web", roles=["admin"], groups=["/tenants/acme"])
    )
    assert "worker" not in user.roles
    ident = Identity.from_user(user)
    assert ident.is_worker is False
    assert ident.tenant_id == "acme"
    assert ident.role == "admin"


def test_worker_jwt_keeps_explicit_extra_roles(monkeypatch):
    """If the realm later assigns extra roles to the service account
    (e.g. via a composite), they're preserved alongside ``worker``."""
    user = _build_user_from_claims(
        _claims(azp="slm-forge-worker", roles=["worker", "read_dataset_extra"])
    )
    assert "worker" in user.roles
    assert "read_dataset_extra" in user.roles