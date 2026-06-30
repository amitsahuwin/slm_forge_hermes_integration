"""Phase C — canonical ``Identity`` derived from the JWT-backed ``User``.

Every authenticated request now resolves to an :class:`Identity` via the
:func:`current_identity` FastAPI dependency. The Identity is what
artifact paths, OPA decisions, and DB scoping all key on — not the raw
JWT or the loose ``User`` model. Single point of mapping means a future
identity-provider swap (or alternate-tenant strategy) touches one file,
not every router.

Field semantics:

* ``tenant_id`` — derived from the first Keycloak group path. A group
  ``/tenants/acme`` yields ``acme``; a group ``/tenants/globex/dept-x``
  also yields ``globex`` (we read only the first non-empty path segment
  after ``tenants``, or — for non-tenants prefixes — the first segment).
* ``user_id`` — the JWT ``sub`` (User.id). Stable across email/role changes.
* ``role`` — the highest-privilege realm role per the precedence list
  (``_ROLE_PRECEDENCE``). The capture-at-write semantics keep historical
  artifact paths stable when a user's role later changes.
* ``is_admin`` / ``is_worker`` — small convenience flags so callers
  don't keep string-matching ``role``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from apps.api.middleware.auth import User


# Precedence order — admin > devops > data_engineer > domain_expert >
# operations > support > viewer > worker. Mirrors policies/role_matrix.rego
# so server-side privilege checks stay consistent.
_ROLE_PRECEDENCE: tuple[str, ...] = (
    "admin",
    "devops",
    "data_engineer",
    "domain_expert",
    "operations",
    "support",
    "viewer",
    "worker",
)

_PRECEDENCE_INDEX: dict[str, int] = {r: i for i, r in enumerate(_ROLE_PRECEDENCE)}


def _highest_role(roles: list[str]) -> str | None:
    """Return the most-privileged role present, or ``None`` if none match."""
    best_idx = len(_ROLE_PRECEDENCE)  # sentinel = lower than anything
    best: str | None = None
    for r in roles:
        idx = _PRECEDENCE_INDEX.get(r)
        if idx is not None and idx < best_idx:
            best_idx = idx
            best = r
    return best


def _tenant_from_groups(groups: list[str]) -> str | None:
    """Extract the tenant segment from a Keycloak group path.

    Accepted shapes:
      ``/tenants/acme``           → ``acme``
      ``/tenants/globex/dept-x``  → ``globex``
      ``/acme``                   → ``acme`` (legacy / non-tenants prefix)
      ``acme``                    → ``acme``
    Empty groups list returns ``None`` — the caller decides whether
    to raise.
    """
    for g in groups:
        parts = [p for p in g.split("/") if p]
        if not parts:
            continue
        if parts[0] == "tenants" and len(parts) >= 2:
            return parts[1]
        return parts[0]
    return None


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    role: str
    email: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    is_admin: bool = False
    is_worker: bool = False

    @classmethod
    def from_user(cls, user: User) -> Identity:
        """Map an authenticated ``User`` → ``Identity``.

        Raises :class:`ValueError` when the user has no tenant assignment
        (configuration error — auth is mandatory and tenant membership
        is implicit in that).
        """
        tenant = _tenant_from_groups(user.groups)
        if not tenant:
            raise ValueError(
                "user has no tenant — assign at least one Keycloak group "
                "under /tenants/<name> before logging in"
            )
        role = _highest_role(user.roles) or "viewer"
        return cls(
            tenant_id=tenant,
            user_id=user.id,
            role=role,
            email=user.email,
            scopes=frozenset(),  # populated in Phase C R4 once worker JWTs land
            is_admin=role == "admin",
            is_worker=role == "worker",
        )


def current_identity(request: Request) -> Identity:
    """FastAPI dependency: pull the canonical Identity for this request.

    Relies on :class:`apps.api.middleware.auth.AuthMiddleware` having
    populated ``request.state.user``. Raises ``401`` when unauthenticated.

    Phase D — when auth is disabled (dev mode) AND the middleware hasn't
    yet attached a user (e.g. tests that exercise the router directly
    without the middleware in the stack), fall back to a synthetic
    admin Identity in tenant=``local``. This preserves the "auth off
    means relaxed" contract while keeping production strictly gated.
    """
    user = getattr(getattr(request, "state", None), "user", None)
    if user is None:
        # Lazy import to avoid circular dependency at module load.
        from apps.api.services.auth_settings import get_auth_settings

        if not get_auth_settings().auth_enabled:
            return Identity(
                tenant_id="local",
                user_id="local-admin",
                role="admin",
                email=None,
                scopes=frozenset(),
                is_admin=True,
                is_worker=False,
            )
        raise HTTPException(401, "Authentication required")
    try:
        return Identity.from_user(user)
    except ValueError as e:
        # Configuration error — surface as 403 rather than 401, since the
        # user IS authenticated, just not assigned to a tenant.
        raise HTTPException(403, str(e)) from e