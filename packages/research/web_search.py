"""Pluggable web search for the R&D engine.

Phase K shipped pure-Ollama generation. K.2 grounds the report in real web
results by hitting one of several search backends, ranked by availability:

1. ``SERPAPI_KEY`` env var → SerpAPI (commercial, key required).
2. ``TAVILY_API_KEY`` env var → Tavily (built for LLM grounding).
3. ``ddgs`` package installed → DuckDuckGo via the unofficial Python lib
   (free, no key, rate-limited).
4. Otherwise → returns ``[]`` and the engine falls back to pure-Ollama mode.

Backends share a single ``SearchHit`` shape so the engine never has to care
which one was used. Failures degrade gracefully — the report still generates,
just without web context.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("research.web_search")

_HTTP_TIMEOUT = 20.0


@dataclass(frozen=True)
class SearchHit:
    title: str
    snippet: str
    url: str
    source: str  # which backend produced this hit


def search(query: str, max_results: int = 5) -> list[SearchHit]:
    """Run a web search using the first available backend.

    Always returns at most ``max_results`` hits. Never raises — failures are
    logged at warning level and an empty list is returned so the caller
    (the research engine) can fall back to pure-LLM generation.
    """
    query = query.strip()
    if not query:
        return []

    # Try in priority order.
    for backend in (_search_serpapi, _search_tavily, _search_ddgs):
        try:
            hits = backend(query, max_results)
        except Exception as e:  # noqa: BLE001
            log.warning("%s search failed: %s", backend.__name__, e)
            continue
        if hits:
            log.info(
                "web search %r returned %d hit(s) via %s",
                query, len(hits), backend.__name__,
            )
            return hits[:max_results]

    log.info("web search %r: no backend available, returning empty", query)
    return []


# ── Backend 1: SerpAPI ────────────────────────────────────────────────────


def _search_serpapi(query: str, max_results: int) -> list[SearchHit]:
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        return []
    r = httpx.get(
        "https://serpapi.com/search.json",
        params={
            "q": query,
            "api_key": key,
            "num": max_results,
            "engine": "google",
        },
        timeout=_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    organic = data.get("organic_results", []) or []
    return [
        SearchHit(
            title=str(h.get("title", "")),
            snippet=str(h.get("snippet", "")),
            url=str(h.get("link", "")),
            source="serpapi",
        )
        for h in organic[:max_results]
    ]


# ── Backend 2: Tavily ─────────────────────────────────────────────────────


def _search_tavily(query: str, max_results: int) -> list[SearchHit]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    r = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        },
        timeout=_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results", []) or []
    return [
        SearchHit(
            title=str(h.get("title", "")),
            snippet=str(h.get("content", h.get("snippet", ""))),
            url=str(h.get("url", "")),
            source="tavily",
        )
        for h in results[:max_results]
    ]


# ── Backend 3: DDGS (no key) ──────────────────────────────────────────────


def _search_ddgs(query: str, max_results: int) -> list[SearchHit]:
    try:
        # `ddgs` (formerly `duckduckgo_search`). Importing lazily so the rest
        # of the module loads even when the package isn't installed yet.
        from ddgs import DDGS  # type: ignore[import-not-found]
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[import-not-found]
        except ImportError:
            return []
    with DDGS() as d:
        raw: list[dict[str, Any]] = list(d.text(query, max_results=max_results))
    return [
        SearchHit(
            title=str(h.get("title", "")),
            snippet=str(h.get("body", h.get("snippet", ""))),
            url=str(h.get("href", h.get("link", ""))),
            source="ddgs",
        )
        for h in raw[:max_results]
    ]


# ── Formatting helpers used by the engine ────────────────────────────────


def format_hits_for_prompt(hits: list[SearchHit]) -> str:
    """Pack hits into a compact context block to prepend to the Ollama prompt."""
    if not hits:
        return ""
    out = ["## Context from the web (use as ground truth)"]
    for i, h in enumerate(hits, 1):
        out.append(
            f"\n[{i}] {h.title}\n    {h.snippet}\n    Source: {h.url}"
        )
    out.append(
        "\nWhen you cite a specific fact from above, refer to its index like [1], [2]."
    )
    return "\n".join(out)


def format_hits_for_sources_section(hits: list[SearchHit]) -> str:
    """Render the trailing Sources section appended to the final report."""
    if not hits:
        return ""
    lines = ["## Sources"]
    for i, h in enumerate(hits, 1):
        title = h.title or h.url
        lines.append(f"- [{i}] [{title}]({h.url})")
    return "\n".join(lines)
