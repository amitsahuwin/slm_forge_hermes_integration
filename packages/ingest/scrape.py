"""Scrape one URL → main content text via trafilatura.

Static HTML only. JS-heavy SPAs won't work (trafilatura sees no rendered text).
For those, the user should save the rendered page and upload it as a file.
"""
from __future__ import annotations

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None  # type: ignore[assignment]


def scrape_url(url: str) -> dict:
    """Fetch one URL, extract main content, return a single row dict.

    Returns: {"url": ..., "title": ..., "content": ...}
    """
    if trafilatura is None:
        raise RuntimeError("trafilatura not installed. Run: uv sync --extra ingest")

    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": "SLM-Forge/0.1 (+local)"})
        r.raise_for_status()
        html = r.text

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if not extracted:
        raise ValueError(
            f"trafilatura found no main content at {url}. "
            "This is usually a JS-heavy SPA — try saving the rendered page and uploading it as a file."
        )

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title
    except Exception:  # noqa: BLE001
        pass

    return {"url": url, "title": title, "content": extracted}
