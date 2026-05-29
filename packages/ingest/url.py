"""Fetch a remote URL and parse as JSONL/CSV/JSON."""
from __future__ import annotations

import httpx

from packages.ingest.local import parse_auto

MAX_BYTES = 100 * 1024 * 1024  # 100 MB hard cap


def fetch_and_parse(url: str) -> tuple[str, list[dict]]:
    """Download the URL and parse it. Returns (format, rows)."""
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        try:
            head = c.head(url)
            cl = head.headers.get("content-length")
            if cl and int(cl) > MAX_BYTES:
                raise ValueError(
                    f"File too large: {int(cl) / 1e6:.1f} MB (max {MAX_BYTES / 1e6:.0f} MB)"
                )
        except httpx.HTTPError:
            pass  # some servers don't support HEAD

        r = c.get(url)
        r.raise_for_status()
        content = r.content
        if len(content) > MAX_BYTES:
            raise ValueError(f"File too large: {len(content) / 1e6:.1f} MB")

    name = url.rsplit("/", 1)[-1] or "download"
    return parse_auto(name, content)
