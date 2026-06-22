"""Tenant boundary resolver.

Hermes traces (and every future tenant-aware row) need a tenant identifier.
The bridge runs in two contexts:

  * **API request handler** — a contextvar bound by ``RequestContextMiddleware``
    carries the tenant for the duration of the request.
  * **Background worker** (ratchet / trainer / exporter) — no request context;
    we fall through to ``SLM_FORGE_TENANT_ID`` (per-worker override), then
    ``SLM_FORGE_DEFAULT_TENANT`` (process-wide default), then the literal
    ``"default"`` so the bridge never raises a "missing tenant" error.

The single-tenant default mirrors the existing data layout (no rows are
filtered by tenant today). The column + helper exist so that flipping a
flag in the future enforces isolation without a schema migration.
"""
from __future__ import annotations

import os

from packages._log_context import tenant_id_ctx

_LITERAL_DEFAULT = "default"


def default_tenant() -> str:
    """Process-wide fallback used by workers and any context that has none."""
    return os.environ.get(
        "SLM_FORGE_TENANT_ID",
        os.environ.get("SLM_FORGE_DEFAULT_TENANT", _LITERAL_DEFAULT),
    )


def current_tenant() -> str:
    """Tenant for the active request/task; falls back to ``default_tenant()``.

    Read from the ``tenant_id`` contextvar set by ``RequestContextMiddleware``.
    Workers don't go through that middleware, so the contextvar is unset and
    we fall through to env-based defaults.
    """
    bound = tenant_id_ctx.get()
    if bound:
        return bound
    return default_tenant()
