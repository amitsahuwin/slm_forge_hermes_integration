"""Probe each seeded tenant bucket via the S3 gateway.

Called by ``make ozone-status`` after a brief port-forward has been
established on ``localhost:9878``. Ozone's S3 gateway does not
implement ``list_buckets`` on the root path; we instead head the four
known demo tenant buckets and report present/missing per-bucket.
"""
from __future__ import annotations

import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

DEMO_TENANTS: tuple[str, ...] = ("local", "acme", "globex", "system")


def main() -> int:
    endpoint = "http://localhost:9878"
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id="slmforge",
            aws_secret_access_key="slmforge-dev-secret",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 1, "mode": "standard"},
            ),
            region_name="us-east-1",
        )
    except Exception as e:  # pragma: no cover — defensive
        print(f"  ? client init failed: {e}", file=sys.stdout)
        return 1

    for tenant in DEMO_TENANTS:
        bucket = f"slm-forge-{tenant}"
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"  ✓ {bucket}")
        except EndpointConnectionError:
            print(f"  ? {bucket} (gateway unreachable)")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchBucket"}:
                print(f"  ✗ {bucket} (missing — run make ozone-bootstrap)")
            else:
                print(f"  ? {bucket} ({code or 'error'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())