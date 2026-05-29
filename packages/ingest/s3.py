"""Fetch an S3 object and parse it. Credentials come from the request payload."""
from __future__ import annotations

import re
from urllib.parse import urlparse

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

from packages.ingest.local import parse_auto

_S3_PATH_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def parse_s3_path(s3_path: str) -> tuple[str, str]:
    """Parse 's3://bucket/key' → (bucket, key). Also accepts https://...amazonaws.com URLs."""
    m = _S3_PATH_RE.match(s3_path)
    if m:
        return m.group(1), m.group(2)
    p = urlparse(s3_path)
    if p.hostname and p.hostname.endswith("amazonaws.com"):
        bucket = p.hostname.split(".")[0]
        key = p.path.lstrip("/")
        return bucket, key
    raise ValueError(f"Could not parse S3 path: {s3_path}")


def fetch_and_parse(
    s3_path: str,
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
) -> tuple[str, list[dict]]:
    """Download s3://bucket/key, parse, return (format, rows)."""
    if boto3 is None:
        raise RuntimeError("boto3 not installed. Run: uv sync --extra ingest")

    bucket, key = parse_s3_path(s3_path)

    session_kwargs: dict = {}
    if access_key and secret_key:
        session_kwargs["aws_access_key_id"] = access_key
        session_kwargs["aws_secret_access_key"] = secret_key
    if region:
        session_kwargs["region_name"] = region

    session = boto3.Session(**session_kwargs)
    s3 = session.client("s3")

    resp = s3.get_object(Bucket=bucket, Key=key)
    content = resp["Body"].read()

    return parse_auto(key, content)
