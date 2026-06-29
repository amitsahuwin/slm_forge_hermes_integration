"""Auth endpoints (Phase M).

* ``GET /api/v1/auth/me``     — current user (synthetic admin in disabled mode).
* ``GET /api/v1/auth/config`` — public bootstrap doc so the SPA can build
  its Keycloak redirect URL.
* ``GET /api/v1/auth/users``  — admin-only proxy to the Keycloak admin
  API; returns 501 if no admin creds are configured.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from apps.api.middleware.auth import User, requires
from apps.api.services.auth_settings import get_auth_settings
from apps.api.services.identity import Identity

log = logging.getLogger("slm_forge.auth.router")
router = APIRouter()


class MeResponse(BaseModel):
    """Phase C — ``/auth/me`` response now carries the resolved Identity
    alongside the raw ``User`` shape. ``tenant_id`` and ``primary_role``
    are what every router scopes on; the SPA reads them to render the
    tenant pill in the nav. Backwards compatible: existing consumers
    that only read ``id``/``email``/``roles``/``groups`` keep working.
    """

    id: str
    email: str | None = None
    roles: list[str] = []
    groups: list[str] = []
    # Phase C — resolved identity fields. ``tenant_id`` may be empty
    # when the user has no tenant group (configuration error after the
    # auth-mandatory cutover, but reported here so the UI can show a
    # readable "configure your tenant" message instead of a blank
    # screen).
    tenant_id: str = ""
    primary_role: str = ""
    is_admin: bool = False
    is_worker: bool = False


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """Whoami — works whether or not enforcement is on.

    Phase C — also returns the resolved Identity so the SPA doesn't have
    to duplicate the role-precedence logic client-side.
    """
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Not authenticated")

    tenant_id = ""
    primary_role = ""
    is_admin = False
    is_worker = False
    try:
        ident = Identity.from_user(user)
        tenant_id = ident.tenant_id
        primary_role = ident.role
        is_admin = ident.is_admin
        is_worker = ident.is_worker
    except ValueError:
        # User has no tenant group — return the raw shape so the UI can
        # render an unauthorized state instead of crashing.
        pass

    return MeResponse(
        id=user.id,
        email=user.email,
        roles=user.roles,
        groups=user.groups,
        tenant_id=tenant_id,
        primary_role=primary_role,
        is_admin=is_admin,
        is_worker=is_worker,
    )


@router.get("/config")
def config() -> dict[str, Any]:
    """Public bootstrap config for the SPA.

    The frontend hits this *before* any auth happens to figure out where
    to send the user for login. We deliberately keep this on the public
    allowlist in the middleware so it works pre-login.
    """
    cfg = get_auth_settings()
    return {
        "auth_enabled": cfg.auth_enabled,
        # IMPORTANT: return the BROWSER-facing URL, not the container-internal
        # one. The SPA runs on the user's machine and can't resolve Docker DNS
        # names like `keycloak:8080`. See KEYCLOAK_PUBLIC_URL in .env.
        "keycloak_url": cfg.keycloak_public_url,
        "realm": cfg.keycloak_realm,
        "web_client_id": cfg.keycloak_web_client_id,
    }


@router.get("/users")
@requires("read", "setting")  # Listing users is an admin-tier operation.
def list_users(request: Request) -> list[dict[str, Any]]:
    """List Keycloak users via the admin API.

    Requires ``KEYCLOAK_ADMIN_USER`` + ``KEYCLOAK_ADMIN_PASSWORD`` env vars.
    Returns 501 if those aren't set — we'd rather be explicit than fake a
    listing.
    """
    cfg = get_auth_settings()
    if not cfg.keycloak_admin_user or not cfg.keycloak_admin_password:
        raise HTTPException(
            501,
            "Feature requires Keycloak admin credentials — set "
            "KEYCLOAK_ADMIN_USER and KEYCLOAK_ADMIN_PASSWORD on the API service.",
        )

    # 1) Grab an admin token from the master realm.
    token_url = (
        f"{cfg.keycloak_url.rstrip('/')}/realms/master/protocol/openid-connect/token"
    )
    try:
        with httpx.Client(timeout=5.0) as client:
            tr = client.post(
                token_url,
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": cfg.keycloak_admin_user,
                    "password": cfg.keycloak_admin_password,
                },
            )
            tr.raise_for_status()
            access = tr.json()["access_token"]

            # 2) Hit the admin users endpoint on our realm.
            users_url = (
                f"{cfg.keycloak_url.rstrip('/')}/admin/realms/"
                f"{cfg.keycloak_realm}/users"
            )
            ur = client.get(
                users_url, headers={"Authorization": f"Bearer {access}"}
            )
            ur.raise_for_status()
            data = ur.json()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("Keycloak admin call failed: %s", e)
        raise HTTPException(502, f"Keycloak admin call failed: {e}") from e

    # Trim to the fields the UI actually wants.
    return [
        {
            "id": u.get("id"),
            "username": u.get("username"),
            "email": u.get("email"),
            "enabled": u.get("enabled"),
            "firstName": u.get("firstName"),
            "lastName": u.get("lastName"),
        }
        for u in data
    ]
