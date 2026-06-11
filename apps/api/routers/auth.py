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

from apps.api.middleware.auth import User, requires
from apps.api.services.auth_settings import get_auth_settings

log = logging.getLogger("slm_forge.auth.router")
router = APIRouter()


@router.get("/me", response_model=User)
def me(request: Request) -> User:
    """Whoami — works whether or not enforcement is on.

    With enforcement off you'll get the synthetic admin; with it on you'll
    get the JWT-derived user the middleware attached.
    """
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        # Shouldn't happen — middleware always attaches one — but be safe.
        raise HTTPException(401, "Not authenticated")
    return user


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
