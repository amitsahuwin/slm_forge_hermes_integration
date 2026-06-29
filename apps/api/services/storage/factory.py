"""Phase D — pick the right backend + apply the disk-fallback decorator.

Behaviour summary:

  SLM_FORGE_STORAGE=local   →  LocalObjectStore rooted at
                                SLM_FORGE_LOCAL_STORAGE_ROOT (default
                                ``/app/storage``).
  SLM_FORGE_STORAGE=s3       →  OzoneObjectStore (Apache Ozone S3 gateway).
  SLM_FORGE_DISK_FALLBACK=true and today < SLM_FORGE_DISK_FALLBACK_UNTIL
                              →  the primary store is wrapped so 404 on
                                 head/get falls through to a LocalObjectStore
                                 rooted at SLM_FORGE_LEGACY_DISK_ROOT
                                 (default ``/app``). Past the sunset
                                 date the flag is ignored.

Key scheme (see :func:`tenant_key`):

  ``{tenant_id}/{role}/{user_id}/{exports|runs|data}/{artifact_id}/{filename}``

Bucket scheme on Ozone:

  ``slm-forge-{tenant_id}`` — created on first login via
  :func:`apps.api.services.storage.tenancy.ensure_tenant_bucket`.

The same key is used unchanged on both backends, just rooted under a
bucket (Ozone) or a path (local). That way a tenant boundary defined
in one place isn't re-stated in every router.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterable, AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from apps.api.services.identity import Identity
from apps.api.services.storage.base import (
    InvalidKey,
    ObjectMeta,
    ObjectNotFound,
    ObjectStore,
    validate_key,
)
from apps.api.services.storage.local import LocalObjectStore

log = logging.getLogger("api.storage")

Kind = Literal["runs", "exports", "data"]
_VALID_KINDS: frozenset[str] = frozenset({"runs", "exports", "data"})

# Filenames may contain dots and dashes, plus the slash separator that
# the caller chose to embed (e.g. ``adapter/x.bin``). Reject anything
# weirder than that.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def tenant_key(
    identity: Identity,
    *,
    kind: Kind,
    artifact_id: int | str,
    filename: str,
) -> str:
    """Produce the canonical key for an artifact.

    Order matters: tenant before role before user before kind. That
    way a directory listing under one tenant is local in the sense
    that the OS will lay out adjacent rows together, and an admin
    glance at the on-disk layout reads bottom-up (artifact ⊂ kind ⊂
    user ⊂ role ⊂ tenant).
    """
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(_VALID_KINDS)}")
    # Filenames can carry one level of internal `/` separators
    # (e.g. ``adapter/x.bin``); we validate each segment individually.
    # Explicit ``..`` / ``.`` rejection — the regex below allows dots
    # so it would otherwise accept ``..`` as a string of two dots.
    for seg in str(filename).split("/"):
        if seg in ("..", "."):
            raise ValueError(
                f"path-traversal segment {seg!r} in filename {filename!r}"
            )
        if not seg or not _SAFE_SEGMENT.match(seg):
            raise ValueError(
                f"unsafe filename segment {seg!r} in {filename!r}; "
                "use only letters/digits/dot/dash/underscore"
            )
    parts = [
        identity.tenant_id,
        identity.role,
        identity.user_id,
        kind,
        str(artifact_id),
        filename,
    ]
    # Defence in depth: feed the assembled key back through the
    # storage validator so a future bug in this builder is caught
    # before it touches disk.
    key = "/".join(parts)
    validate_key(key)
    return key


# ─── factory ────────────────────────────────────────────────────────────


def _local_root() -> Path:
    return Path(os.environ.get("SLM_FORGE_LOCAL_STORAGE_ROOT", "/app/storage"))


def _legacy_root() -> Path:
    return Path(os.environ.get("SLM_FORGE_LEGACY_DISK_ROOT", "/app"))


def _disk_fallback_active() -> bool:
    if os.environ.get("SLM_FORGE_DISK_FALLBACK", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return False
    until = os.environ.get("SLM_FORGE_DISK_FALLBACK_UNTIL", "").strip()
    if not until:
        return False
    try:
        sunset = date.fromisoformat(until)
    except ValueError:
        log.warning(
            "SLM_FORGE_DISK_FALLBACK_UNTIL=%r is not ISO-8601; disabling fallback",
            until,
        )
        return False
    return datetime.now(UTC).date() < sunset


def _primary_store() -> ObjectStore:
    backend = os.environ.get("SLM_FORGE_STORAGE", "s3").strip().lower()
    if backend == "local":
        return LocalObjectStore(root=_local_root())
    # ``s3`` is the default. Import is lazy because aioboto3 is heavy
    # and only one of the two implementations is used per process.
    from apps.api.services.storage.ozone import OzoneObjectStore

    return OzoneObjectStore()


def get_object_store(identity: Identity) -> ObjectStore:
    """Return the storage backend the caller should use.

    The result is per-request: callers should not cache it across
    tenant boundaries. The Ozone implementation uses identity to pick
    the bucket; the local implementation embeds tenant in the key.
    """
    base = _primary_store()
    if _disk_fallback_active():
        return DiskFallbackStore(base, fallback=LocalObjectStore(root=_legacy_root()))
    return base


# ─── 30-day disk fallback decorator ─────────────────────────────────────


class DiskFallbackStore(ObjectStore):
    """Wrap a primary store; on ``head``/``get`` 404 fall through to
    a :class:`LocalObjectStore` rooted at the legacy disk layout.

    The key transform strips the Phase D ``{tenant}/{role}/{user}/``
    prefix and keeps only the ``{kind}/{artifact_id}/{filename}``
    suffix so the lookup hits the existing on-disk paths. Writes go
    only to the primary store; deletes go only to the primary store
    (we never mutate legacy data).
    """

    def __init__(self, primary: ObjectStore, *, fallback: LocalObjectStore) -> None:
        self._inner = primary
        self._fallback = fallback

    @staticmethod
    def _strip_tenant_prefix(key: str) -> str | None:
        """Translate a Phase-D key into a legacy disk path.

        Phase-D key: ``{tenant}/{role}/{user}/{kind}/{id}/{filename}``
        Legacy disk: ``{kind}/{id}/{filename}``

        Returns ``None`` when the key doesn't have a recognisable
        ``{kind}`` segment — we won't guess.
        """
        parts = key.split("/")
        # Find the first segment that is a recognised storage kind
        # (``runs``, ``exports``, ``data``).
        for i, seg in enumerate(parts):
            if seg in _VALID_KINDS:
                return "/".join(parts[i:])
        return None

    async def put(
        self,
        key: str,
        body: AsyncIterable[bytes],
        *,
        content_type: str | None,
    ) -> ObjectMeta:
        return await self._inner.put(key, body, content_type=content_type)

    def get(self, key: str) -> AsyncIterator[bytes]:
        # Try the primary store first; if it raises ``ObjectNotFound``,
        # we fall through to the legacy disk lookup. We can't easily
        # peek inside an async generator before iterating, so we
        # ``head`` first to decide which one to stream from.
        async def _stream() -> AsyncIterator[bytes]:
            try:
                meta = await self._inner.head(key)
            except ObjectNotFound:
                meta = None
            if meta is not None:
                async for chunk in self._inner.get(key):
                    yield chunk
                return
            legacy = self._strip_tenant_prefix(key)
            if legacy is None:
                raise ObjectNotFound(key)
            try:
                async for chunk in self._fallback.get(legacy):
                    yield chunk
            except (ObjectNotFound, InvalidKey) as e:
                raise ObjectNotFound(key) from e
            log.warning("disk-fallback served %s ← %s", key, legacy)

        return _stream()

    async def head(self, key: str) -> ObjectMeta | None:
        primary = await self._inner.head(key)
        if primary is not None:
            return primary
        legacy = self._strip_tenant_prefix(key)
        if legacy is None:
            return None
        try:
            fb = await self._fallback.head(legacy)
        except InvalidKey:
            return None
        return ObjectMeta(
            key=key,
            size=fb.size,
            content_type=fb.content_type,
            etag=fb.etag,
            last_modified=fb.last_modified,
        ) if fb else None

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)  # never mutate legacy disk

    async def list(self, prefix: str, limit: int = 1000) -> list[ObjectMeta]:
        # Tenant queries only — the legacy disk has no concept of
        # tenant prefix, so listing across it would leak.
        return await self._inner.list(prefix, limit=limit)