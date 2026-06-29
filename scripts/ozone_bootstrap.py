"""Phase D — bootstrap the two demo tenant buckets in a fresh Ozone.

Run after ``make ozone-up`` once all Ozone pods are ``Running``::

    make ozone-bootstrap

Creates the ``slm-forge`` volume (Ozone-native) and the per-tenant
S3 buckets used by the realm seed (``slm-forge-local``,
``slm-forge-acme``, ``slm-forge-globex``, ``slm-forge-system``).

Idempotent: a bucket that already exists is skipped without error.
The script reads the same env vars as the runtime
``OzoneObjectStore`` so a working ``.env`` is enough.
"""
from __future__ import annotations

import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

DEMO_TENANTS: tuple[str, ...] = ("local", "acme", "globex", "system")


def main() -> int:
    endpoint = (
        os.environ.get("SLM_FORGE_OZONE_S3_ENDPOINT", "").strip()
        or "http://localhost:9878"
    )
    access_key = os.environ.get("SLM_FORGE_OZONE_ACCESS_KEY_ID", "slmforge")
    secret_key = os.environ.get(
        "SLM_FORGE_OZONE_SECRET_ACCESS_KEY", "slmforge-dev-secret"
    )

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
        region_name="us-east-1",
    )

    print(f"Bootstrapping Ozone S3 gateway at {endpoint} …", file=sys.stderr)
    for tenant in DEMO_TENANTS:
        bucket = f"slm-forge-{tenant}"
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"  ✓ {bucket} (already exists)", file=sys.stderr)
            continue
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
                # On many Ozone builds, head returns 403/Forbidden on
                # a bucket the caller doesn't own — treat as "needs
                # creation" rather than dying.
                pass
        try:
            s3.create_bucket(Bucket=bucket)
            print(f"  + {bucket} (created)", file=sys.stderr)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                print(f"  ✓ {bucket} (already exists)", file=sys.stderr)
                continue
            print(f"  ✗ {bucket} failed: {e}", file=sys.stderr)
            return 1

    print("\nBuckets ready. Try: aws --endpoint-url=%s s3 ls" % endpoint, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())