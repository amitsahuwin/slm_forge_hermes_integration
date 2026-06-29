"""Auth runtime configuration (Phase M).

Reads env vars once at process start and exposes them as a frozen pydantic
``AuthSettings`` singleton. Everything in the auth stack — JWT verification,
OPA policy checks, the ``/auth/config`` endpoint, the ``@requires`` decorator
— pulls from this single source so toggling enforcement is a one-env-var
operation.

The most important knob is ``SLM_FORGE_AUTH_ENABLED``. It defaults to
``false`` so the existing local-dev workflow is bit-for-bit unchanged: every
request is treated as a synthetic admin and no JWT/OPA calls are made.
Flip it to ``true`` (in ``docker-compose.yml`` or a shell export) to bring
Keycloak + OPA into the request path.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import BaseModel

log = logging.getLogger("api.auth_settings")


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var the way operators expect.

    Accepts the usual truthy strings ("1", "true", "yes", "on") in any
    case. Anything else — including unset — falls back to ``default``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class AuthSettings(BaseModel):
    """Frozen view of the auth env vars."""

    # Master enforcement switch. False = synthetic admin, no JWT, no OPA.
    auth_enabled: bool = False

    # Keycloak coordinates — defaults match the docker-compose service names.
    # `keycloak_url` is the **container-internal** URL used by the API to
    # fetch JWKS + verify tokens. `keycloak_public_url` is what we return to
    # the browser via /api/v1/auth/config — the browser is on the host so
    # `keycloak:8080` (Docker DNS) won't resolve. Defaults to localhost:8080
    # which works for `docker compose --profile auth up` on a Mac.
    keycloak_url: str = "http://keycloak:8080"
    keycloak_public_url: str = "http://localhost:8080"
    keycloak_realm: str = "slm-forge"
    keycloak_web_client_id: str = "slm-forge-web"
    keycloak_api_client_id: str = "slm-forge-api"

    # OPA coordinates.
    opa_url: str = "http://opa:8181"
    opa_decision_path: str = "/v1/data/slm_forge/allow"
    opa_timeout_seconds: float = 0.2  # 200ms, matches the spec.

    # Used when auth is disabled — keeps log lines consistent.
    default_user: str = "anonymous"

    # Service-account shared secret. Host workers (trainer/ratchet/exporter)
    # send this in `X-Service-Token` so they don't need a JWT. Empty string
    # disables the bypass entirely. Must match SLM_FORGE_SERVICE_TOKEN in
    # the workers' environment.
    service_token: str = ""

    # Optional Keycloak admin credentials for the /auth/users endpoint. When
    # unset, the endpoint returns 501 instead of trying to fake a listing.
    keycloak_admin_user: str | None = None
    keycloak_admin_password: str | None = None

    @property
    def realm_base_url(self) -> str:
        """Convenience: ``<keycloak_url>/realms/<realm>`` (container-internal).

        Used for server-to-server traffic from inside the API container —
        most importantly JWKS fetching, which has to work from Docker's
        internal DNS.
        """
        return f"{self.keycloak_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def public_realm_base_url(self) -> str:
        """``<keycloak_public_url>/realms/<realm>`` — browser-facing URL."""
        return f"{self.keycloak_public_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def jwks_url(self) -> str:
        # Always fetched from inside the container → use the internal URL.
        return f"{self.realm_base_url}/protocol/openid-connect/certs"

    @property
    def issuer(self) -> str:
        # The `iss` claim of every token Keycloak issues echoes the URL the
        # SPA hit, which is the PUBLIC URL (browser uses localhost:8080).
        # Validating against the internal URL produces a hard-to-debug 401
        # mismatch — so we explicitly use the public form here.
        return self.public_realm_base_url


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    """Module-level singleton — cheap, immutable, easy to monkeypatch in tests."""
    auth_enabled = _env_bool("SLM_FORGE_AUTH_ENABLED", default=False)
    if not auth_enabled:
        # Phase C — multi-tenancy makes auth-disable a foot-gun: every
        # request becomes the synthetic admin which bypasses tenant
        # isolation entirely. Loud once-per-process warning so operators
        # notice they're running in legacy mode. Removal of the flag is
        # tracked in `docs/specs/2026-06-29-multi-tenancy-identity.md`.
        log.warning(
            "SLM_FORGE_AUTH_ENABLED=false — DEPRECATED. Auth-disable mode "
            "bypasses tenant isolation. Use `make auth ENABLED=true` and "
            "`make auth-token EMAIL=alice@acme` for dev iteration. This "
            "flag will be removed in a future release."
        )
    return AuthSettings(
        auth_enabled=auth_enabled,
        keycloak_url=os.environ.get("KEYCLOAK_URL", "http://keycloak:8080"),
        keycloak_public_url=os.environ.get(
            "KEYCLOAK_PUBLIC_URL", "http://localhost:8080"
        ),
        keycloak_realm=os.environ.get("KEYCLOAK_REALM", "slm-forge"),
        keycloak_web_client_id=os.environ.get(
            "KEYCLOAK_WEB_CLIENT_ID", "slm-forge-web"
        ),
        keycloak_api_client_id=os.environ.get(
            "KEYCLOAK_API_CLIENT_ID", "slm-forge-api"
        ),
        opa_url=os.environ.get("OPA_URL", "http://opa:8181"),
        opa_decision_path=os.environ.get(
            "OPA_DECISION_PATH", "/v1/data/slm_forge/allow"
        ),
        opa_timeout_seconds=float(os.environ.get("OPA_TIMEOUT_SECONDS", "0.2")),
        default_user=os.environ.get("SLM_FORGE_DEFAULT_USER", "anonymous"),
        service_token=os.environ.get("SLM_FORGE_SERVICE_TOKEN", ""),
        keycloak_admin_user=os.environ.get("KEYCLOAK_ADMIN_USER") or None,
        keycloak_admin_password=os.environ.get("KEYCLOAK_ADMIN_PASSWORD") or None,
    )
