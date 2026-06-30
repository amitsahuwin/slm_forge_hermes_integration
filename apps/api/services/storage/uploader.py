"""Phase D follow-up — sweep local artifact dirs to the object store.

Workers (both local-mode MLX and remote CUDA) write training artifacts
to ``/app/runs/<run_id>/`` directly. The upload-via-API endpoint is
optional. Without a post-completion sync, those artifacts never reach
Ozone even when ``SLM_FORGE_STORAGE=s3``.

This module exposes a single helper that walks a local directory and
uploads each regular file to the configured store under the canonical
key scheme (``tenant_key``). Invoked by ``patch_run`` as a background
task when a run transitions to COMPLETED.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from apps.api.services.identity import Identity
from apps.api.services.storage.factory import get_object_store, tenant_key
from apps.api.services.storage.tenancy import ensure_tenant_bucket

log = logging.getLogger("api.storage.uploader")

# Files we DON'T copy to S3 — noisy logs that the operator can still
# pull from the runs container via `docker logs slm-forge-api`.
_SKIP_NAMES: frozenset[str] = frozenset({".DS_Store"})


async def _async_file_iter(p: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """aioboto3 wants async-iterable bodies; wrap a file with one."""
    with p.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                return
            yield chunk


def storage_backend_name() -> str:
    """Return the configured storage backend ('local' or 's3'). Exposed
    so the lifespan startup log and any future /storage/info endpoint
    can report the same string the factory acts on."""
    return os.environ.get("SLM_FORGE_STORAGE", "s3").strip().lower()


async def sync_local_dir_to_store(
    local_dir: Path,
    *,
    identity: Identity,
    kind: str,
    artifact_id: int | str,
) -> dict[str, int]:
    """Recursively walk ``local_dir`` and upload each regular file to the
    object store under ``tenant_key(identity, kind=kind, artifact_id=...,
    filename=<relative path>)``.

    Skipped when ``SLM_FORGE_STORAGE=local`` (already-local writes don't
    need a sync). Returns a ``{files, bytes, skipped}`` counter for the
    background-task logger.
    """
    counters = {"files": 0, "bytes": 0, "skipped": 0}
    if storage_backend_name() == "local":
        log.debug("storage backend is 'local' — no S3 sync needed for %s", local_dir)
        return counters

    if not local_dir.exists() or not local_dir.is_dir():
        log.info("sync skipped — %s does not exist", local_dir)
        return counters

    try:
        await ensure_tenant_bucket(identity)
    except Exception as e:  # pragma: no cover — defensive
        log.warning(
            "sync skipped — ensure_tenant_bucket(%s) failed: %s",
            identity.tenant_id,
            e,
        )
        return counters

    store = get_object_store(identity)

    for p in sorted(local_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name in _SKIP_NAMES:
            counters["skipped"] += 1
            continue
        # Relative path becomes the filename portion of the key.
        rel = p.relative_to(local_dir).as_posix()
        try:
            key = tenant_key(
                identity, kind=kind, artifact_id=artifact_id, filename=rel
            )
        except ValueError as e:
            # Path-traversal or unsafe segment — should be impossible
            # under our controlled write paths but skip safely if so.
            log.warning("sync skipped %s (%s) — unsafe filename", p, e)
            counters["skipped"] += 1
            continue

        size = p.stat().st_size
        try:
            await store.put(key, _async_file_iter(p), content_type=None)
            counters["files"] += 1
            counters["bytes"] += size
            log.info(
                "storage sync: %s → %s (%d bytes)",
                p.relative_to(local_dir),
                key,
                size,
            )
        except Exception as e:  # pragma: no cover — log + continue
            log.warning("storage sync failed for %s: %s", p, e)
            counters["skipped"] += 1

    log.info(
        "storage sync complete: kind=%s artifact_id=%s files=%d bytes=%d skipped=%d",
        kind,
        artifact_id,
        counters["files"],
        counters["bytes"],
        counters["skipped"],
    )
    return counters