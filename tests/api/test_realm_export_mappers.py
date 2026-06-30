"""Contract test for keycloak/realm-export.json.

Locks in the protocol-mapper config the SPA needs so the tenant pill
keeps working. Without a Group Membership mapper on ``slm-forge-web``,
Keycloak issues access tokens with no ``groups`` claim, the API
resolves ``tenant_id=""``, and the UI badge regresses to ``no tenant``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REALM_PATH = (
    Path(__file__).resolve().parents[2] / "keycloak" / "realm-export.json"
)


@pytest.fixture(scope="module")
def realm() -> dict:
    with REALM_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _clients_by_id(realm: dict) -> dict[str, dict]:
    return {c["clientId"]: c for c in realm.get("clients", [])}


def test_realm_export_loads(realm: dict) -> None:
    assert realm.get("realm") == "slm-forge"


def test_slm_forge_web_has_group_membership_mapper(realm: dict) -> None:
    """Tenant pill bug 2026-06-30: this mapper is what puts the
    ``groups`` claim into the access token the SPA sends back to
    ``/api/v1/auth/me``. Without it, every tenant user pills "no tenant".
    """
    web = _clients_by_id(realm).get("slm-forge-web")
    assert web is not None, "slm-forge-web client missing from realm export"

    mappers = web.get("protocolMappers") or []
    group_mappers = [
        m for m in mappers
        if m.get("protocolMapper") == "oidc-group-membership-mapper"
    ]
    assert group_mappers, (
        "slm-forge-web must define an oidc-group-membership-mapper so "
        "tokens carry a `groups` claim — without it the tenant pill shows "
        "'no tenant' for every user."
    )

    cfg = group_mappers[0].get("config") or {}
    assert cfg.get("claim.name") == "groups", (
        "Mapper must emit the claim under the name 'groups' "
        "(apps/api/middleware/auth.py:185 reads claims['groups'])."
    )
    assert cfg.get("access.token.claim") in {"true", True}, (
        "Mapper must populate the ACCESS token — the API verifies the "
        "access token, not the id token."
    )
    assert cfg.get("full.path") in {"true", True}, (
        "Mapper must emit full group paths (e.g. /tenants/acme); the "
        "Identity resolver parses the 'tenants/<name>' segment."
    )


def test_tenant_users_belong_to_a_tenant_group(realm: dict) -> None:
    """Pin the tenant assignments so future realm edits don't silently
    drop a user out of their tenant."""
    expected = {
        "alice@acme": "/tenants/acme",
        "bob@acme": "/tenants/acme",
        "viewer@acme": "/tenants/acme",
        "carol@globex": "/tenants/globex",
        "dave@globex": "/tenants/globex",
        "viewer@globex": "/tenants/globex",
        "admin@local": "/tenants/local",
    }
    users = {u["username"]: u for u in realm.get("users", [])}
    for username, group in expected.items():
        assert username in users, f"seed user {username} missing from realm export"
        assert group in (users[username].get("groups") or []), (
            f"{username} should be in group {group}"
        )