"""Phase D — Apache Ozone S3-gateway implementation of `ObjectStore`.

Uses ``aioboto3`` against the gateway exposed by the
``ozone-helm-charts`` install (see ``deploy/ozone/values.yaml``).
Per-tenant bucket naming + key scheme:

  bucket = ``slm-forge-{tenant_id}``
  key    = ``{role}/{user_id}/{exports|runs|data}/{artifact_id}/{filename}``

Buckets are created on first login by
:func:`apps.api.services.storage.tenancy.ensure_tenant_bucket`.

The aioboto3 import is intentionally lazy so processes that don't use
the Ozone backend (e.g. tests under ``SLM_FORGE_STORAGE=local``) don't
pay the import cost. The factory selects this class only when
``SLM_FORGE_STORAGE=s3``.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from apps.api.services.storage.base import (
    ObjectMeta,
    ObjectNotFound,
    ObjectStore,
    validate_key,
)

log = logging.getLogger("api.storage.ozone")

DEFAULT_BUCKET_PREFIX = "slm-forge"
# Streaming chunk sizes — match LocalObjectStore for consistent RSS
# behaviour during large uploads/downloads.
_READ_CHUNK = 64 * 1024


def _bucket_for(tenant_id: str) -> str:
    """S3 bucket name for a tenant. We do not embed the role/user
    because those move with the user (capture-at-write semantics) and
    buckets are stable per tenant."""
    return f"{DEFAULT_BUCKET_PREFIX}-{tenant_id}"


class OzoneObjectStore(ObjectStore):
    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._endpoint = (
            endpoint_url
            or os.environ.get("SLM_FORGE_OZONE_S3_ENDPOINT", "")
            or "http://host.docker.internal:9878"
        )
        self._access_key = access_key_id or os.environ.get(
            "SLM_FORGE_OZONE_ACCESS_KEY_ID", ""
        )
        self._secret_key = secret_access_key or os.environ.get(
            "SLM_FORGE_OZONE_SECRET_ACCESS_KEY", ""
        )
        self._region = region

    # ─── session helper ──────────────────────────────────────────────

    def _session(self) -> Any:
        # Lazy import — aioboto3 + botocore are >40 MB combined.
        import aioboto3

        return aioboto3.Session(
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    def _client(self) -> Any:
        # Path-style addressing — Ozone's S3 gateway has historically
        # had quirks with virtual-hosted style.
        from botocore.config import Config

        return self._session().client(
            "s3",
            endpoint_url=self._endpoint,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        """Split ``{tenant}/{rest}`` into ``(bucket, object_key)``.

        Phase D keys always start with the tenant segment. This avoids
        passing the bucket separately to every method — the key is
        self-describing.
        """
        validate_key(key)
        tenant, sep, rest = key.partition("/")
        if not sep or not rest:
            raise ObjectNotFound(
                f"key {key!r} missing tenant prefix (expected '<tenant>/<rest>')"
            )
        return _bucket_for(tenant), rest

    # ─── ObjectStore interface ───────────────────────────────────────

    async def put(
        self,
        key: str,
        body: AsyncIterable[bytes],
        *,
        content_type: str | None,
    ) -> ObjectMeta:
        bucket, object_key = self._split_key(key)
        total = 0
        # aioboto3's ``put_object`` accepts ``Body=`` as bytes-or-stream.
        # We materialise into a BytesIO only when small; for true
        # streaming uploads (>5 MB), we drive the Multipart Upload API
        # directly — that's the only way to keep RSS flat at 1 GB.
        async with self._client() as s3:
            mp = await s3.create_multipart_upload(
                Bucket=bucket,
                Key=object_key,
                ContentType=content_type or "application/octet-stream",
            )
            upload_id = mp["UploadId"]
            try:
                parts: list[dict[str, Any]] = []
                # Aggregate stream chunks into ≥5MiB part buffers
                # (S3 minimum part size, last part excepted).
                part_buf = bytearray()
                part_num = 1
                MIN_PART = 5 * 1024 * 1024
                async for chunk in body:
                    if not chunk:
                        continue
                    part_buf.extend(chunk)
                    total += len(chunk)
                    if len(part_buf) >= MIN_PART:
                        resp = await s3.upload_part(
                            Bucket=bucket,
                            Key=object_key,
                            PartNumber=part_num,
                            UploadId=upload_id,
                            Body=bytes(part_buf),
                        )
                        parts.append({"ETag": resp["ETag"], "PartNumber": part_num})
                        part_num += 1
                        part_buf.clear()
                if part_buf or part_num == 1:
                    # Always upload a final part — S3 requires ≥1 part.
                    resp = await s3.upload_part(
                        Bucket=bucket,
                        Key=object_key,
                        PartNumber=part_num,
                        UploadId=upload_id,
                        Body=bytes(part_buf),
                    )
                    parts.append({"ETag": resp["ETag"], "PartNumber": part_num})
                await s3.complete_multipart_upload(
                    Bucket=bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except BaseException:
                await s3.abort_multipart_upload(
                    Bucket=bucket, Key=object_key, UploadId=upload_id
                )
                raise
        return ObjectMeta(key=key, size=total, content_type=content_type)

    def get(self, key: str) -> AsyncIterator[bytes]:
        bucket, object_key = self._split_key(key)

        async def _stream() -> AsyncIterator[bytes]:
            async with self._client() as s3:
                try:
                    resp = await s3.get_object(Bucket=bucket, Key=object_key)
                except Exception as e:  # noqa: BLE001
                    # aioboto3 raises botocore ``ClientError`` with
                    # ``Code == 'NoSuchKey'`` on miss; we normalise.
                    if "NoSuchKey" in str(e) or "404" in str(e):
                        raise ObjectNotFound(key) from e
                    raise
                async for chunk in resp["Body"].iter_chunks(_READ_CHUNK):
                    yield chunk

        return _stream()

    async def head(self, key: str) -> ObjectMeta | None:
        bucket, object_key = self._split_key(key)
        async with self._client() as s3:
            try:
                resp = await s3.head_object(Bucket=bucket, Key=object_key)
            except Exception as e:  # noqa: BLE001
                if "404" in str(e) or "NoSuchKey" in str(e):
                    return None
                raise
        return ObjectMeta(
            key=key,
            size=int(resp.get("ContentLength", 0)),
            content_type=resp.get("ContentType"),
            etag=(resp.get("ETag") or "").strip('"') or None,
            last_modified=str(resp.get("LastModified") or "") or None,
        )

    async def delete(self, key: str) -> None:
        bucket, object_key = self._split_key(key)
        async with self._client() as s3:
            try:
                await s3.delete_object(Bucket=bucket, Key=object_key)
            except Exception as e:  # noqa: BLE001
                if "404" in str(e) or "NoSuchKey" in str(e):
                    return
                raise

    async def list(self, prefix: str, limit: int = 1000) -> list[ObjectMeta]:
        # Phase D prefixes always include the tenant segment as the
        # first path component; we route to the matching bucket.
        validate_key(prefix.rstrip("/") + "/x")  # smuggle to validate
        tenant, _, object_prefix = prefix.partition("/")
        bucket = _bucket_for(tenant)
        async with self._client() as s3:
            try:
                resp = await s3.list_objects_v2(
                    Bucket=bucket, Prefix=object_prefix, MaxKeys=limit
                )
            except Exception as e:  # noqa: BLE001
                if "NoSuchBucket" in str(e):
                    return []
                raise
        rows: list[ObjectMeta] = []
        for entry in resp.get("Contents", []) or []:
            rows.append(
                ObjectMeta(
                    key=f"{tenant}/{entry['Key']}",
                    size=int(entry.get("Size", 0)),
                    content_type=None,
                    etag=(entry.get("ETag") or "").strip('"') or None,
                    last_modified=str(entry.get("LastModified") or "") or None,
                )
            )
        return rows

    async def presign_get(self, key: str, ttl_seconds: int) -> str:
        bucket, object_key = self._split_key(key)
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": object_key},
                ExpiresIn=ttl_seconds,
            )

    async def presign_put(self, key: str, ttl_seconds: int) -> str:
        bucket, object_key = self._split_key(key)
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket, "Key": object_key},
                ExpiresIn=ttl_seconds,
            )