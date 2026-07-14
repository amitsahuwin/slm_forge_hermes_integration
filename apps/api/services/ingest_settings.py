"""Ingest upload-size configuration (large-dataset-upload).

Two knobs, read once at process start and exposed as a frozen pydantic
singleton (same pattern as ``auth_settings``):

* ``SLM_FORGE_MAX_UPLOAD_BYTES`` — hard cap for the async *large* upload path,
  default 500 MB. Enforced both against ``Content-Length`` and the running
  byte total while streaming to the object store.
* ``SLM_FORGE_INGEST_SYNC_MAX_BYTES`` — threshold below which the existing
  synchronous ``POST /ingest/file`` path is used; also the frontend's routing
  boundary. Default 10 MB.

Both are validated at first read and fail fast (``ValueError``) on an
unparseable / non-positive value, or if the sync threshold exceeds the hard
cap (a nonsensical routing boundary).
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel

_DEFAULT_MAX_UPLOAD_BYTES = 524_288_000  # 500 MB
_DEFAULT_SYNC_MAX_BYTES = 10_485_760  # 10 MB


class IngestSettings(BaseModel):
    """Frozen view of the ingest upload-size env vars."""

    model_config = {"frozen": True}

    max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES
    sync_max_bytes: int = _DEFAULT_SYNC_MAX_BYTES


def _parse_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


@lru_cache(maxsize=1)
def get_ingest_settings() -> IngestSettings:
    """Module-level singleton — cheap, immutable, easy to monkeypatch in tests."""
    max_upload_bytes = _parse_positive_int(
        "SLM_FORGE_MAX_UPLOAD_BYTES", _DEFAULT_MAX_UPLOAD_BYTES
    )
    sync_max_bytes = _parse_positive_int(
        "SLM_FORGE_INGEST_SYNC_MAX_BYTES", _DEFAULT_SYNC_MAX_BYTES
    )
    if sync_max_bytes > max_upload_bytes:
        raise ValueError(
            "SLM_FORGE_INGEST_SYNC_MAX_BYTES "
            f"({sync_max_bytes}) must not exceed SLM_FORGE_MAX_UPLOAD_BYTES "
            f"({max_upload_bytes})"
        )
    return IngestSettings(
        max_upload_bytes=max_upload_bytes,
        sync_max_bytes=sync_max_bytes,
    )
