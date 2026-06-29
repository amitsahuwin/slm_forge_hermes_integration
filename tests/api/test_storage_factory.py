"""Phase D — `get_object_store` picks the right backend per env +
the tenant-scoped key prefixer wraps every put/get/head/delete with
the canonical ``{role}/{user_id}/{exports|runs|data}/<artifact>``
layout.

Tenants are isolated by *both* bucket (Ozone) and key prefix (local),
so a forgotten scope_query somewhere upstream cannot land a write in
the wrong tenant's space — the prefixer rejects keys that don't
already start with the caller's tenant.
"""
from __future__ import annotations

import pytest

from apps.api.middleware.auth import User
from apps.api.services.identity import Identity


async def _gen(payload: bytes):
    yield payload


async def _collect(stream) -> bytes:
    parts: list[bytes] = []
    async for c in stream:
        parts.append(c)
    return b"".join(parts)


def _identity(tenant: str, user: str, role: str) -> Identity:
    u = User(id=user, roles=[role], groups=[f"/tenants/{tenant}"])
    return Identity.from_user(u)


@pytest.fixture()
def factory_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    monkeypatch.setenv("SLM_FORGE_LOCAL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("SLM_FORGE_DISK_FALLBACK", raising=False)
    yield tmp_path


def test_factory_local_returns_local_store(factory_env):
    from apps.api.services.storage.factory import get_object_store
    from apps.api.services.storage.local import LocalObjectStore

    store = get_object_store(_identity("acme", "alice", "admin"))
    assert isinstance(store, LocalObjectStore) or hasattr(store, "_inner")


@pytest.mark.asyncio
async def test_tenant_key_for_run_artifact(factory_env):
    from apps.api.services.storage.factory import (
        get_object_store,
        tenant_key,
    )

    ident = _identity("acme", "alice", "admin")
    key = tenant_key(ident, kind="runs", artifact_id=42, filename="adapter.bin")
    assert key == "acme/admin/alice/runs/42/adapter.bin"

    store = get_object_store(ident)
    await store.put(key, _gen(b"abc"), content_type="application/octet-stream")
    out = await _collect(store.get(key))
    assert out == b"abc"


def test_tenant_key_supports_exports_and_data(factory_env):
    from apps.api.services.storage.factory import tenant_key

    ident = _identity("globex", "carol", "data_engineer")
    assert (
        tenant_key(ident, kind="exports", artifact_id=9, filename="model.gguf")
        == "globex/data_engineer/carol/exports/9/model.gguf"
    )
    assert (
        tenant_key(ident, kind="data", artifact_id="ds-v1", filename="train.jsonl")
        == "globex/data_engineer/carol/data/ds-v1/train.jsonl"
    )


def test_tenant_key_rejects_unknown_kind(factory_env):
    from apps.api.services.storage.factory import tenant_key

    ident = _identity("acme", "alice", "admin")
    with pytest.raises(ValueError, match="kind"):
        tenant_key(ident, kind="secrets", artifact_id=1, filename="x")  # type: ignore[arg-type]


def test_tenant_key_sanitises_filename_path_traversal(factory_env):
    """`..` in the filename must not produce a key that escapes the
    tenant prefix. The factory rejects, not silently rewrites."""
    from apps.api.services.storage.factory import tenant_key

    ident = _identity("acme", "alice", "admin")
    with pytest.raises(ValueError):
        tenant_key(ident, kind="runs", artifact_id=42, filename="../escape")


@pytest.mark.asyncio
async def test_disk_fallback_decorator_returns_legacy_on_404(
    tmp_path, monkeypatch
):
    """When `SLM_FORGE_DISK_FALLBACK=true`, a missing key in the primary
    store falls through to a `LocalObjectStore` rooted at the legacy
    `/app/...` paths."""
    legacy = tmp_path / "legacy"
    primary = tmp_path / "primary"
    legacy.mkdir()
    primary.mkdir()
    # Plant a legacy artifact under the runs subtree.
    (legacy / "runs").mkdir()
    (legacy / "runs" / "42").mkdir()
    (legacy / "runs" / "42" / "x.bin").write_bytes(b"legacy-bytes")

    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    monkeypatch.setenv("SLM_FORGE_LOCAL_STORAGE_ROOT", str(primary))
    monkeypatch.setenv("SLM_FORGE_DISK_FALLBACK", "true")
    monkeypatch.setenv("SLM_FORGE_DISK_FALLBACK_UNTIL", "2030-01-01")
    monkeypatch.setenv("SLM_FORGE_LEGACY_DISK_ROOT", str(legacy))

    from apps.api.services.storage.factory import get_object_store

    ident = _identity("acme", "alice", "admin")
    store = get_object_store(ident)
    # Direct legacy-path lookup — the fallback rewrites a tenant-keyed
    # GET back to the bare legacy layout when primary returns 404.
    meta = await store.head("acme/admin/alice/runs/42/x.bin")
    assert meta is not None, "fallback didn't surface the legacy artifact"
    out = await _collect(store.get("acme/admin/alice/runs/42/x.bin"))
    assert out == b"legacy-bytes"


@pytest.mark.asyncio
async def test_disk_fallback_ignored_after_sunset(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    monkeypatch.setenv("SLM_FORGE_LOCAL_STORAGE_ROOT", str(tmp_path / "primary"))
    monkeypatch.setenv("SLM_FORGE_DISK_FALLBACK", "true")
    monkeypatch.setenv("SLM_FORGE_DISK_FALLBACK_UNTIL", "2020-01-01")
    monkeypatch.setenv(
        "SLM_FORGE_LEGACY_DISK_ROOT", str(tmp_path / "ignored-legacy")
    )
    (tmp_path / "primary").mkdir()

    from apps.api.services.storage.factory import get_object_store

    ident = _identity("acme", "alice", "admin")
    store = get_object_store(ident)
    # Even if we plant a legacy artifact, sunset means it's invisible.
    (tmp_path / "ignored-legacy").mkdir()
    legacy_file = tmp_path / "ignored-legacy" / "runs" / "42"
    legacy_file.mkdir(parents=True)
    (legacy_file / "x.bin").write_bytes(b"x")
    meta = await store.head("acme/admin/alice/runs/42/x.bin")
    assert meta is None