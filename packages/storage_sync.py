"""Upload local run/export artifacts to the Ozone S3 bucket.

Workers (trainer, exporter) write artifacts to the local filesystem.
When ``SLM_FORGE_STORAGE=s3``, this module copies the local artifacts
into the appropriate Ozone bucket so the API (and any remote reader)
can find them.

Usage from workers::

    from packages.storage_sync import sync_run_artifacts
    from packages.storage_sync import sync_export_artifacts

    # After a training run completes:
    sync_run_artifacts(run_id=42)

    # After an export completes:
    sync_export_artifacts(export_id=7)

No-op when ``SLM_FORGE_STORAGE`` is not ``s3``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("storage_sync")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / "runs"
EXPORTS_ROOT = PROJECT_ROOT / "exports"

_BUCKET_PREFIX = "slm-forge"

# ──────────────────────────────────────────────────────────


def _s3_enabled() -> bool:
    return (
        os.environ.get("SLM_FORGE_STORAGE", "").strip().lower()
        == "s3"
    )


def _endpoint_url() -> str:
    """Resolve the S3 endpoint for the *host* environment.

    ``SLM_FORGE_OZONE_S3_ENDPOINT`` may contain
    ``host.docker.internal`` which doesn't resolve on the host.
    Replace it with ``localhost`` when running outside Docker.
    """
    url = os.environ.get(
        "SLM_FORGE_OZONE_S3_ENDPOINT", "http://localhost:9878"
    )
    return url.replace("host.docker.internal", "localhost")


def _s3_client():
    """Build a synchronous boto3 S3 client for Ozone."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=os.environ.get(
            "SLM_FORGE_OZONE_ACCESS_KEY_ID", ""
        ),
        aws_secret_access_key=os.environ.get(
            "SLM_FORGE_OZONE_SECRET_ACCESS_KEY", ""
        ),
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _ensure_bucket(client, bucket: str) -> None:
    """Create the bucket if it doesn't exist (idempotent)."""
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001
        try:
            client.create_bucket(Bucket=bucket)
            log.info("Created bucket %s", bucket)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "create_bucket(%s) failed (may already exist): %s",
                bucket, exc,
            )


def _upload_directory(
    client,
    bucket: str,
    local_dir: Path,
    s3_prefix: str,
) -> int:
    """Recursively upload ``local_dir`` into ``bucket/s3_prefix/``.

    Returns the number of files uploaded.
    """
    count = 0
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir)
        key = f"{s3_prefix}/{rel}"
        try:
            client.upload_file(str(path), bucket, key)
            count += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Failed to upload %s → s3://%s/%s: %s",
                path, bucket, key, exc,
            )
    return count


# ── public API ──────────────────────────────────────────


def sync_run_artifacts(
    run_id: int,
    *,
    tenant_id: str | None = None,
) -> None:
    """Upload ``runs/<run_id>/`` to the tenant's Ozone bucket.

    ``tenant_id`` determines the bucket (``slm-forge-{tenant}``).
    Falls back to ``SLM_FORGE_DEFAULT_TENANT`` / ``"default"`` if not
    provided.
    """
    if not _s3_enabled():
        return

    run_dir = RUNS_ROOT / str(run_id)
    if not run_dir.exists():
        log.warning(
            "sync_run_artifacts: run dir %s does not exist", run_dir,
        )
        return

    tenant = tenant_id or os.environ.get(
        "SLM_FORGE_DEFAULT_TENANT", "default"
    )
    bucket = f"{_BUCKET_PREFIX}-{tenant}"
    prefix = f"runs/{run_id}"

    log.info(
        "Syncing run #%s → s3://%s/%s/", run_id, bucket, prefix,
    )
    try:
        client = _s3_client()
        _ensure_bucket(client, bucket)
        n = _upload_directory(client, bucket, run_dir, prefix)
        log.info(
            "Synced run #%s: %d files → s3://%s/%s/",
            run_id, n, bucket, prefix,
        )
    except Exception:
        log.exception("sync_run_artifacts failed for run #%s", run_id)


def sync_export_artifacts(
    export_id: int,
    *,
    tenant_id: str | None = None,
) -> None:
    """Upload ``exports/<export_id>/`` to the tenant's Ozone bucket.

    ``tenant_id`` determines the bucket (``slm-forge-{tenant}``).
    Falls back to ``SLM_FORGE_DEFAULT_TENANT`` / ``"default"`` if not
    provided.
    """
    if not _s3_enabled():
        return

    export_dir = EXPORTS_ROOT / str(export_id)
    if not export_dir.exists():
        log.warning(
            "sync_export_artifacts: export dir %s does not exist",
            export_dir,
        )
        return

    tenant = tenant_id or os.environ.get(
        "SLM_FORGE_DEFAULT_TENANT", "default"
    )
    bucket = f"{_BUCKET_PREFIX}-{tenant}"
    prefix = f"exports/{export_id}"

    log.info(
        "Syncing export #%s → s3://%s/%s/",
        export_id, bucket, prefix,
    )
    try:
        client = _s3_client()
        _ensure_bucket(client, bucket)
        n = _upload_directory(client, bucket, export_dir, prefix)
        log.info(
            "Synced export #%s: %d files → s3://%s/%s/",
            export_id, n, bucket, prefix,
        )
    except Exception:
        log.exception(
            "sync_export_artifacts failed for export #%s", export_id,
        )
