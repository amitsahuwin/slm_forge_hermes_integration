"""Phase D — `LocalObjectStore` — filesystem-backed implementation.

This is the default when ``SLM_FORGE_STORAGE=local`` and the substrate
of the 30-day disk-fallback decorator. The on-disk layout mirrors the
key: a key like ``acme/admin/alice/runs/42/x.bin`` becomes
``<root>/acme/admin/alice/runs/42/x.bin``. The root is provided by the
factory and varies between request-tenant and the legacy fallback
roots (``/app/runs``, ``/app/exports``, ``/app/data``).

Writes are atomic per file: bytes stream into ``<final>.partial`` and
``os.replace`` flips it into place on success. Concurrent writers to
the same key see the last-writer-wins semantics native to the file
system; this matches Ozone's S3 gateway behaviour and is what the
upstream routers already assume.
"""
from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterable, AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from apps.api.services.storage.base import (
    InvalidKey,
    ObjectMeta,
    ObjectNotFound,
    ObjectStore,
    validate_key,
)

# 64 KiB matches the streaming-upload spec and is large enough to keep
# `read()` calls cheap on Linux without bloating peak RSS during a
# multi-GB upload.
_READ_CHUNK = 64 * 1024


class LocalObjectStore(ObjectStore):
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ─── internal helpers ────────────────────────────────────────────

    def _resolve(self, key: str) -> Path:
        validate_key(key)
        path = (self._root / key).resolve()
        # Defence-in-depth: after resolving, double-check we're still
        # inside ``self._root``. ``validate_key`` already rejects ``..``
        # but this guards against future symlink shenanigans.
        try:
            path.relative_to(self._root)
        except ValueError as e:
            raise InvalidKey(f"key escapes root: {key!r}") from e
        return path

    @staticmethod
    def _guess_content_type(key: str, override: str | None) -> str | None:
        if override:
            return override
        guess, _ = mimetypes.guess_type(key)
        return guess

    # ─── ObjectStore interface ───────────────────────────────────────

    async def put(
        self,
        key: str,
        body: AsyncIterable[bytes],
        *,
        content_type: str | None,
    ) -> ObjectMeta:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".partial")
        total = 0
        try:
            with tmp.open("wb") as f:
                async for chunk in body:
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return ObjectMeta(
            key=key,
            size=total,
            content_type=self._guess_content_type(key, content_type),
            etag=None,
            last_modified=datetime.now(UTC).isoformat(),
        )

    def get(self, key: str) -> AsyncIterator[bytes]:
        # Sync wrapper returning an async generator so callers
        # ``async for chunk in store.get(key)`` works without an extra
        # ``await``. Ozone's S3 client matches this shape.
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFound(key)

        async def _stream() -> AsyncIterator[bytes]:
            with path.open("rb") as f:
                while True:
                    chunk = f.read(_READ_CHUNK)
                    if not chunk:
                        return
                    yield chunk

        return _stream()

    async def head(self, key: str) -> ObjectMeta | None:
        path = self._resolve(key)
        if not path.exists():
            return None
        st = path.stat()
        return ObjectMeta(
            key=key,
            size=st.st_size,
            content_type=self._guess_content_type(key, None),
            etag=None,
            last_modified=datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
        )

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            path.unlink(missing_ok=True)
        except IsADirectoryError:
            # Idempotent — directories may exist when a key shares a
            # prefix with another; we only remove file leaves.
            return

    async def list(self, prefix: str, limit: int = 1000) -> list[ObjectMeta]:
        # ``prefix`` is treated as a key prefix, NOT as a directory
        # path; matches S3 semantics. Walk the root and filter.
        rows: list[ObjectMeta] = []
        for p in sorted(self._root.rglob("*")):
            if not p.is_file():
                continue
            key = str(p.relative_to(self._root))
            if not key.startswith(prefix):
                continue
            st = p.stat()
            rows.append(
                ObjectMeta(
                    key=key,
                    size=st.st_size,
                    content_type=self._guess_content_type(key, None),
                    etag=None,
                    last_modified=datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                )
            )
            if len(rows) >= limit:
                break
        return rows