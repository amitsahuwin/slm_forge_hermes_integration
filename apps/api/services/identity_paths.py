"""Phase D.3 — identity-scoped filesystem paths.

Where the DB-backed routers use ``scope_query``, the file-based routers
(research, synth, ingest, datasets) need to gate filesystem access by
caller identity. This module centralises the layout:

    {ROOT}/global/{name}/                  → shared, read-only (bundled samples)
    {ROOT}/users/{tenant_id}/{user_id}/...  → per-user, writable

and the path-discovery helpers that decide which dirs a given Identity
may see. Non-admin users see ``global/`` ∪ their own dir; tenant admins
see ``global/`` ∪ every user under their tenant; workers and the
synthetic admin see everything they need to to operate.

Path-traversal rejection lives here too — any name coming in over the
wire must be sanitised before being concatenated with one of these
roots.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.api.services.identity import Identity


# ─── Roots ──────────────────────────────────────────────────────────────
# Configurable so dev machines can point them outside /app. Defaults
# match the Docker bind mounts.

REPORTS_ROOT = Path(os.environ.get("SLM_FORGE_REPORTS_ROOT", "/app/docs/market-research"))
DATASETS_ROOT = Path(os.environ.get("SLM_FORGE_DATASETS_ROOT", "/app/data/datasets"))
INGEST_STAGING_ROOT = Path(
    os.environ.get("SLM_FORGE_INGEST_STAGING_ROOT", "/app/data/.ingest_staging")
)

# Filename / dataset-name safety. Allow alnum + `-_.` ; reject `..`,
# absolute paths, leading dots (hidden files), and any path separator.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_.]*$")


def safe_name(name: str) -> str:
    """Return ``name`` if it's safe to join with a root, else raise
    ``ValueError``. Caller decides how to surface (typically HTTP 400).

    Used for client-supplied names (dataset names, report filenames).
    Identity-derived components (``tenant_id``, ``user_id``) come from
    the validated JWT and use :func:`safe_identity_component` instead,
    which permits ``@`` to keep email-shaped user IDs working.
    """
    if not name or len(name) > 200:
        raise ValueError("name must be 1–200 characters")
    if name.startswith("."):
        raise ValueError("name cannot start with '.'")
    if not _NAME_RE.match(name):
        raise ValueError(
            "name may only contain letters, digits, '-', '_', '.'"
        )
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("name cannot contain path separators or '..'")
    return name


_IDENTITY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_.@]*$")


def safe_identity_component(value: str) -> str:
    """Validate a tenant_id / user_id before joining to a path. More
    permissive than :func:`safe_name` — allows ``@`` so usernames like
    ``alice@acme`` work as path segments. Still rejects path traversal,
    separators, and hidden-file prefixes."""
    if not value or len(value) > 200:
        raise ValueError("identity component must be 1–200 characters")
    if value.startswith("."):
        raise ValueError("identity component cannot start with '.'")
    if not _IDENTITY_RE.match(value):
        raise ValueError(
            "identity component may only contain letters, digits, '-', '_', '.', '@'"
        )
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("identity component cannot contain path separators or '..'")
    return value


# ─── Per-root layout helpers ────────────────────────────────────────────


def _global_dir(root: Path) -> Path:
    return root / "global"


def _users_root(root: Path) -> Path:
    return root / "users"


def _user_dir(root: Path, identity: Identity) -> Path:
    tenant = safe_identity_component(identity.tenant_id)
    user = safe_identity_component(identity.user_id)
    return _users_root(root) / tenant / user


def _tenant_users_dir(root: Path, identity: Identity) -> Path:
    tenant = safe_identity_component(identity.tenant_id)
    return _users_root(root) / tenant


# ─── Dataset paths ──────────────────────────────────────────────────────


def user_datasets_dir(identity: Identity) -> Path:
    """The caller's writable dataset directory. Created on demand by
    the caller; this function doesn't ``mkdir`` so dry-run paths stay
    side-effect-free."""
    return _user_dir(DATASETS_ROOT, identity)


def global_datasets_dir() -> Path:
    """Where ``scripts/seed_datasets.py`` writes the bundled samples.
    Read-only at the API layer; the wipe-clean script preserves it."""
    return _global_dir(DATASETS_ROOT)


def visible_dataset_dirs(identity: Identity) -> list[Path]:
    """Directories the caller may list datasets from. Order matters —
    earlier entries win for name collisions (``global/foo`` shadows a
    same-named user dataset)."""
    out: list[Path] = [global_datasets_dir()]
    if identity.is_admin:
        # Tenant-admin sees every user under their tenant.
        tenant_root = _tenant_users_dir(DATASETS_ROOT, identity)
        out.append(tenant_root)
    else:
        out.append(user_datasets_dir(identity))
    return out


def resolve_dataset(name: str, identity: Identity) -> Path | None:
    """Find ``name`` across the caller's visible dataset dirs. Returns
    the first hit (global first) or ``None`` if not found / not allowed.
    Raises ``ValueError`` on a malformed name."""
    safe_name(name)
    # Global is one level deep: global/<name>/
    g = global_datasets_dir() / name
    if g.is_dir():
        return g
    if identity.is_admin:
        # Walk every user dir under tenant.
        tenant_root = _tenant_users_dir(DATASETS_ROOT, identity)
        if tenant_root.is_dir():
            for user_dir in tenant_root.iterdir():
                candidate = user_dir / name
                if candidate.is_dir():
                    return candidate
    else:
        c = user_datasets_dir(identity) / name
        if c.is_dir():
            return c
    return None


# ─── Report paths ───────────────────────────────────────────────────────


def user_reports_dir(identity: Identity) -> Path:
    return _user_dir(REPORTS_ROOT, identity)


def visible_report_dirs(identity: Identity) -> list[Path]:
    """Directories the caller may list reports from.

    Strictly tenant-scoped — pre-Phase-D-3 flat layout is NOT included
    (Phase D requires ``make wipe-clean`` on upgrade, which removes it).
    """
    if identity.is_admin:
        return [_tenant_users_dir(REPORTS_ROOT, identity)]
    return [user_reports_dir(identity)]


def resolve_report(filename: str, identity: Identity) -> Path | None:
    """Find a report by filename across the caller's visible dirs."""
    safe_name(filename)
    if identity.is_admin:
        tenant_root = _tenant_users_dir(REPORTS_ROOT, identity)
        if tenant_root.is_dir():
            for user_dir in tenant_root.iterdir():
                p = user_dir / filename
                if p.is_file():
                    return p
    else:
        p = user_reports_dir(identity) / filename
        if p.is_file():
            return p
    return None


# ─── Ingest staging ─────────────────────────────────────────────────────


def user_staging_dir(identity: Identity) -> Path:
    return _user_dir(INGEST_STAGING_ROOT, identity)