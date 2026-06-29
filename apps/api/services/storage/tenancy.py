"""Phase D — idempotent ``slm-forge-{tenant_id}`` bucket lifecycle.

Called from the auth middleware (after a successful JWT verification)
so the first request from a freshly-onboarded tenant doesn't 404 on
its very first artifact write. Idempotent: ``HeadBucket`` first,
``CreateBucket`` only on miss.

On the ``SLM_FORGE_STORAGE=local`` path this is a no-op — local
storage just creates directories as keys arrive.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from apps.api.services.identity import Identity

log = logging.getLogger("api.storage.tenancy")

DEFAULT_BUCKET_PREFIX = "slm-forge"


def _bucket_for(tenant_id: str) -> str:
    return f"{DEFAULT_BUCKET_PREFIX}-{tenant_id}"


async def ensure_tenant_bucket(identity: Identity) -> str:
    """Ensure ``slm-forge-{tenant_id}`` exists on the active backend.

    Returns the bucket name. No-op on ``SLM_FORGE_STORAGE=local`` (the
    local backend uses directories under
    ``SLM_FORGE_LOCAL_STORAGE_ROOT``; first write creates them).
    """
    backend = os.environ.get("SLM_FORGE_STORAGE", "s3").strip().lower()
    bucket = _bucket_for(identity.tenant_id)
    if backend == "local":
        return bucket

    # Ozone path — lazy import so local-only deployments don't pay it.
    from apps.api.services.storage.ozone import OzoneObjectStore

    store = OzoneObjectStore()
    async with store._client() as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            return bucket
        except Exception:  # noqa: BLE001
            pass
        try:
            await s3.create_bucket(Bucket=bucket)
            log.info("created Ozone bucket %s for tenant %s", bucket, identity.tenant_id)
        except Exception as e:  # noqa: BLE001
            # Concurrent first-login may race with another worker; if
            # the bucket exists now we treat the create as a success.
            log.warning("create_bucket(%s) failed; assuming it exists: %s", bucket, e)
    return bucket


# ─── compatibility helpers used by routers ──────────────────────────────


def bucket_name(identity: Identity) -> str:
    """Pure helper — bucket name only, no side effects."""
    return _bucket_for(identity.tenant_id)


def _client_kwargs() -> dict[str, Any]:
    """Surfaced for tests that want to instantiate a one-off client
    without going through the global factory."""
    return {
        "endpoint_url": os.environ.get("SLM_FORGE_OZONE_S3_ENDPOINT", ""),
        "aws_access_key_id": os.environ.get("SLM_FORGE_OZONE_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": os.environ.get(
            "SLM_FORGE_OZONE_SECRET_ACCESS_KEY", ""
        ),
        "region_name": "us-east-1",
    }