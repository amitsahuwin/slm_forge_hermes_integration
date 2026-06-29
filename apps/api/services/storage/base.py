"""Phase D — `ObjectStore` ABC + supporting types.

The interface is async and streaming on both sides so a 1 GB upload
doesn't materialise the archive in API RAM. Implementations:

  * :class:`apps.api.services.storage.local.LocalObjectStore` — disk
  * :class:`apps.api.services.storage.ozone.OzoneObjectStore` — S3 via aioboto3
  * :class:`apps.api.services.storage.factory.DiskFallbackStore`
    decorator that falls through to a `LocalObjectStore` on 404 while
    `SLM_FORGE_DISK_FALLBACK` is true and the sunset date has not passed.

Errors are signalled via narrow exception types rather than HTTP
codes: routers translate :class:`ObjectNotFound` to 404,
:class:`InvalidKey` to 400, etc. The store itself stays HTTP-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass


class StorageError(Exception):
    """Base class for storage failures."""


class ObjectNotFound(StorageError):
    """Raised by ``get`` (and used by ``head`` as the negative case)."""


class InvalidKey(StorageError):
    """The key is malformed (empty, absolute, contains ``..``, etc.).

    Implementations MUST validate before touching storage.
    """


@dataclass(frozen=True)
class ObjectMeta:
    key: str
    size: int
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None  # ISO-8601 when available


def validate_key(key: str) -> None:
    """Centralised key check. Implementations call this before any I/O.

    Rules:
      * non-empty
      * no leading ``/`` (keys are relative)
      * no ``..`` segments (prevents disk-mode path traversal)
      * no backslashes (Windows-style separators)
      * no null bytes
    """
    if not key:
        raise InvalidKey("empty key")
    if key.startswith("/"):
        raise InvalidKey(f"absolute key not allowed: {key!r}")
    if "\\" in key:
        raise InvalidKey(f"backslash in key not allowed: {key!r}")
    if "\x00" in key:
        raise InvalidKey(f"null byte in key not allowed: {key!r}")
    for seg in key.split("/"):
        if seg in ("..", "."):
            raise InvalidKey(f"path-traversal segment {seg!r} in key {key!r}")


class ObjectStore(ABC):
    """Tenant-scoped object storage with streaming put/get.

    Implementations are constructed by
    :func:`apps.api.services.storage.factory.get_object_store` from the
    request's :class:`Identity`; the identity determines the bucket
    (Ozone) or root subdirectory (local).
    """

    @abstractmethod
    async def put(
        self,
        key: str,
        body: AsyncIterable[bytes],
        *,
        content_type: str | None,
    ) -> ObjectMeta:
        """Stream ``body`` into ``key``; return the resulting metadata."""

    @abstractmethod
    def get(self, key: str) -> AsyncIterator[bytes]:
        """Stream the value at ``key``. Raises :class:`ObjectNotFound`
        if absent. Returned iterator can be ``async for``-ed."""

    @abstractmethod
    async def head(self, key: str) -> ObjectMeta | None:
        """Return metadata or ``None`` on miss. Never raises ``ObjectNotFound``."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Idempotent — no error when the key doesn't exist."""

    @abstractmethod
    async def list(self, prefix: str, limit: int = 1000) -> list[ObjectMeta]:
        """Return rows whose key starts with ``prefix``, sorted by key."""

    async def presign_get(self, key: str, ttl_seconds: int) -> str:
        """Optional. Return a signed GET URL with the given TTL.

        Implementations that don't support presigning may return an
        empty string; the router then falls back to streaming through
        the API.
        """
        return ""

    async def presign_put(self, key: str, ttl_seconds: int) -> str:
        """Optional, mirror of :meth:`presign_get`."""
        return ""