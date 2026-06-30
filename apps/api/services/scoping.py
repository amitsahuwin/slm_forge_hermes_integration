"""Phase C — ``scope_query`` adds tenant + owner WHERE clauses.

Behaviour matrix:

  * admin in tenant X     → rows where ``tenant_id == X``
  * non-admin in tenant X → rows where ``tenant_id == X AND user_id == self``
  * worker                → ``WHERE 1=0`` (no rows). Workers operate on
    individual runs they have claimed via ``POST /runs/claim``;
    listing endpoints must never serve them rows, even by accident.

A single helper means forgetting it in a new router becomes the only
mistake to look for — there's no per-call surface to drift on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from sqlalchemy.sql import false

if TYPE_CHECKING:
    from apps.api.services.identity import Identity

T = TypeVar("T")


def scope_query(query: T, identity: Identity, model: type) -> T:
    """Return ``query`` narrowed to the rows ``identity`` is allowed to see.

    ``model`` must expose ``tenant_id`` and ``user_id`` columns (added
    by the Phase C migration). When either column is missing on the
    model, callers see a hard error at import time rather than a silent
    cross-tenant leak.
    """
    # Hard-fail at call time if the model hasn't been migrated.
    if not hasattr(model, "tenant_id") or not hasattr(model, "user_id"):
        raise TypeError(
            f"scope_query: {model.__name__} is missing tenant_id/user_id columns; "
            "add them via the Phase C migration before scoping."
        )

    # System-tenant admins (service-token workers) need cross-tenant
    # access to poll queued jobs from any tenant. They bypass scoping.
    if identity.tenant_id == "system" and identity.is_admin:
        return query

    if identity.is_worker:
        # Non-admin workers must use the claim queue path.
        return query.where(false())  # type: ignore[attr-defined]

    q = query.where(model.tenant_id == identity.tenant_id)  # type: ignore[attr-defined]
    if not identity.is_admin:
        q = q.where(model.user_id == identity.user_id)  # type: ignore[attr-defined]
    return q
