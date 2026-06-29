"""Phase D — `LocalObjectStore` is the filesystem-backed implementation of
:class:`apps.api.services.storage.base.ObjectStore`. It's the default
when ``SLM_FORGE_STORAGE=local``, and is also the substrate for the
30-day disk-fallback decorator that lets pre-Phase-D artifacts remain
readable while operators migrate.

Contract:
  * keys are POSIX-style strings; ``/`` is the only separator
  * ``put`` is a streaming write (chunked iterable of bytes)
  * ``get`` is a streaming read (async iterator of bytes)
  * ``head`` returns ``None`` on miss instead of raising
  * ``delete`` is idempotent (no error if absent)
  * ``list`` returns sorted ``ObjectMeta`` rows
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


async def _collect(stream) -> bytes:
    parts: list[bytes] = []
    async for chunk in stream:
        parts.append(chunk)
    return b"".join(parts)


async def _gen(chunks: list[bytes]):
    for c in chunks:
        yield c


@pytest.fixture()
def store(tmp_path: Path):
    from apps.api.services.storage.local import LocalObjectStore

    return LocalObjectStore(root=tmp_path)


@pytest.mark.asyncio
async def test_put_then_get_roundtrips_bytes(store):
    payload = b"hello world\n" * 32
    meta = await store.put(
        "tenants/acme/admin/alice/runs/42/adapter/x.bin",
        _gen([payload]),
        content_type="application/octet-stream",
    )
    assert meta.size == len(payload)
    assert meta.key == "tenants/acme/admin/alice/runs/42/adapter/x.bin"

    out = await _collect(store.get(meta.key))
    assert out == payload


@pytest.mark.asyncio
async def test_put_supports_chunked_stream(store):
    chunks = [b"a" * 8, b"b" * 8, b"c" * 4]
    meta = await store.put("k1.bin", _gen(chunks), content_type=None)
    assert meta.size == 20
    out = await _collect(store.get("k1.bin"))
    assert out == b"".join(chunks)


@pytest.mark.asyncio
async def test_head_returns_none_on_miss(store):
    assert await store.head("nope/no/here") is None


@pytest.mark.asyncio
async def test_head_returns_meta_on_hit(store):
    await store.put("a/b/c.txt", _gen([b"abc"]), content_type="text/plain")
    meta = await store.head("a/b/c.txt")
    assert meta is not None
    assert meta.size == 3
    assert meta.content_type == "text/plain"


@pytest.mark.asyncio
async def test_delete_is_idempotent(store):
    await store.put("doomed", _gen([b"x"]), content_type=None)
    await store.delete("doomed")
    await store.delete("doomed")  # no error
    assert await store.head("doomed") is None


@pytest.mark.asyncio
async def test_list_returns_sorted_prefix_matches(store):
    for k in ["p/a", "p/b", "p/sub/c", "other/x"]:
        await store.put(k, _gen([b"."]), content_type=None)
    rows = await store.list("p/")
    keys = [m.key for m in rows]
    assert keys == sorted(keys)
    assert "other/x" not in keys
    assert "p/sub/c" in keys


@pytest.mark.asyncio
async def test_get_raises_when_key_missing(store):
    from apps.api.services.storage.base import ObjectNotFound

    with pytest.raises(ObjectNotFound):
        async for _ in store.get("missing"):
            pass


@pytest.mark.asyncio
async def test_key_rejects_path_traversal(store):
    """`..` and absolute paths must not escape the root."""
    from apps.api.services.storage.base import InvalidKey

    bad = [
        "../escape",
        "a/../../escape",
        "/absolute",
        "",  # empty
    ]
    for key in bad:
        with pytest.raises(InvalidKey):
            await store.put(key, _gen([b"x"]), content_type=None)