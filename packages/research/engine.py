"""Market research engine — Ollama-driven outline → sections → markdown report.

Pure Python, side-effect free except for the explicit ``report_path(...).parent.mkdir``
inside callers (the API router decides when/where to persist). Each Ollama call
is a single blocking ``httpx.post`` so the router runs us in ``asyncio.to_thread``.

The contract for ``build_report``:

1.  Ask for a 5-7 section outline (JSON).
2.  Ask for prose body of each section (plain text, depth-controlled length).
3.  If depth == ``"deep"``, run a self-critique pass that produces an
    "Open questions" section.
4.  Always end with a "Comparison table" — a markdown table comparing the
    topic to 3-5 alternatives or competitors.
5.  Compose a single markdown document with YAML frontmatter and return it.

Failures inside any single section are tolerated and replaced with an
``_(section failed: <error>)_`` placeholder so the report still completes.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from packages.research.web_search import (
    SearchHit,
    format_hits_for_prompt,
    format_hits_for_sources_section,
    search as web_search,
)

log = logging.getLogger("research.engine")

Depth = Literal["quick", "standard", "deep"]
ProgressEvent = dict[str, Any]
ProgressCb = Callable[[ProgressEvent], None]

# How many search hits to inject as context. Tunable per-depth.
_WEB_HITS: dict[str, int] = {"quick": 4, "standard": 6, "deep": 10}

# Mounted via ./docs:/app/docs in docker-compose (see integration patches).
_REPORTS_DIR = Path("/app/docs/market-research")

# Word target per section, by depth.
_WORD_TARGETS: dict[Depth, int] = {
    "quick": 200,
    "standard": 400,
    "deep": 600,
}

_HTTP_TIMEOUT = 600.0  # seconds — generous, deep sections on small hardware are slow.


# ─── Path helpers ────────────────────────────────────────────────────────


def slugify(title: str) -> str:
    """Lowercase, alphanumeric + hyphens. Collapses runs and trims edges.

    Empty / pathological inputs collapse to ``"report"`` so the filename is
    always well-formed.
    """
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "report"


def report_path(reports_dir: Path, title: str, when: datetime | None = None) -> Path:
    """Build ``<reports_dir>/<YYYYMMDD-HHMMSS>-<slug>.md``."""
    ts = (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return reports_dir / f"{ts}-{slugify(title)}.md"


# ─── Ollama call ─────────────────────────────────────────────────────────


def _post_ollama(
    *,
    system: str,
    user: str,
    model: str,
    ollama_url: str,
    expect_json: bool = False,
    temperature: float = 0.5,
) -> str:
    """Single blocking ``httpx.post`` to Ollama's ``/api/chat`` endpoint.

    Mirrors the shape used by ``packages.ratchet.hermes_bridge._call_ollama`` but
    is kept independent so we can vary temperature / format per call.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }
    if expect_json:
        payload["format"] = "json"

    r = httpx.post(f"{ollama_url}/api/chat", json=payload, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"]


# ─── Stage prompts ───────────────────────────────────────────────────────


_OUTLINE_SYSTEM = (
    "You are a senior market analyst. Produce a JSON outline for a market "
    "research report. Return ONLY JSON of the shape "
    '{"sections": ["Section 1 title", "Section 2 title", ...]}. '
    "5-7 sections, each a short noun-phrase title (no numbering), covering: "
    "market overview, key players / alternatives, differentiators, adoption / "
    "traction, risks, and strategic outlook. Tailor titles to the topic."
)


def _outline_user(topic: str, depth: Depth) -> str:
    return json.dumps(
        {
            "topic": topic,
            "depth": depth,
            "instruction": (
                "Return JSON only. 5-7 section titles. No prose, no markdown, "
                "no code fences."
            ),
        }
    )


def _section_system(words: int) -> str:
    return (
        "You are a senior market analyst writing one section of a market "
        f"research report. Write plain prose (no headings, no lists, no "
        f"markdown fences) of roughly {words} words. Be concrete: cite real "
        "products, vendors, numbers, and dates when you can. If uncertain, "
        "say so plainly rather than inventing numbers. Do not repeat the "
        "section title."
    )


def _section_user(topic: str, section_title: str, prior_sections: list[str]) -> str:
    return json.dumps(
        {
            "topic": topic,
            "section_title": section_title,
            "prior_section_titles": prior_sections,
            "instruction": (
                "Write the body of this section only. Plain prose paragraphs. "
                "Avoid repeating content already covered in prior sections."
            ),
        }
    )


_CRITIQUE_SYSTEM = (
    "You are a skeptical research editor. Read the draft report and identify "
    "the 3-6 weakest claims, missing data points, or open strategic questions. "
    "Output a plain markdown bullet list — one bullet per item — under the "
    "implicit heading 'Open questions'. Do NOT output the heading itself, "
    "just the bullets. Each bullet should be one sentence."
)


def _critique_user(topic: str, draft: str) -> str:
    # Keep the draft we send modest so deep-think models don't OOM.
    truncated = draft if len(draft) < 12000 else draft[:12000] + "\n\n...[truncated]"
    return json.dumps(
        {
            "topic": topic,
            "instruction": (
                "Identify weak claims, missing data, or open questions in this "
                "report. Output a markdown bullet list only."
            ),
            "draft_report": truncated,
        }
    )


_COMPARISON_SYSTEM = (
    "You are a market analyst. Produce a single GitHub-flavored markdown table "
    "comparing the topic to 3-5 close alternatives or competitors. Columns "
    "should be: Name, One-line summary, Strengths, Weaknesses, Best fit. "
    "Output ONLY the table — no prose before or after, no code fences, no "
    "explanation. The first row is the topic itself."
)


def _comparison_user(topic: str) -> str:
    return json.dumps(
        {
            "topic": topic,
            "instruction": (
                "Markdown table only. 4-6 rows total (topic + 3-5 alternatives). "
                "Keep cells short — under ~12 words each."
            ),
        }
    )


# ─── Parsing helpers ─────────────────────────────────────────────────────


def _parse_outline(raw: str) -> list[str]:
    """Tolerant JSON parse of an outline response — returns section titles.

    Falls back to a generic 5-section outline if parsing fails so the report
    can still proceed end-to-end.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Outline JSON parse failed; raw=%s", raw[:200])
        return _fallback_outline()

    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, list):
        return _fallback_outline()

    cleaned: list[str] = []
    for s in sections:
        if isinstance(s, str) and s.strip():
            cleaned.append(s.strip())
    if len(cleaned) < 3:
        return _fallback_outline()
    # Cap at 7 to keep report length bounded.
    return cleaned[:7]


def _fallback_outline() -> list[str]:
    return [
        "Market Overview",
        "Key Players and Alternatives",
        "Differentiators",
        "Adoption and Traction",
        "Risks and Open Problems",
    ]


def _strip_leading_heading(text: str, title: str) -> str:
    """Models sometimes ignore "no heading" — strip a top-of-section heading."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip():
            stripped = ln.lstrip("#").strip()
            if stripped.lower() == title.lower():
                return "\n".join(lines[i + 1 :]).lstrip("\n")
            break
    return text.strip()


# ─── Top-level orchestration ─────────────────────────────────────────────


def build_report(
    topic: str,
    depth: Depth,
    model: str,
    ollama_url: str,
    progress_cb: ProgressCb | None = None,
) -> str:
    """Run the full research pipeline and return the composed markdown body.

    Errors inside a single Ollama call are caught and replaced with a small
    italic placeholder so partial reports are still useful. A failure in the
    outline stage, however, falls back to ``_fallback_outline()`` rather than
    raising — the caller is responsible for higher-level error handling.
    """

    def _emit(ev: ProgressEvent) -> None:
        if progress_cb is not None:
            try:
                progress_cb(ev)
            except Exception:  # noqa: BLE001
                log.exception("progress_cb raised; continuing")

    topic_clean = topic.strip()
    if not topic_clean:
        raise ValueError("topic must be non-empty")

    # ── 0. Web grounding (K.2) ──────────────────────────────────────────
    # Free, no-key DuckDuckGo by default; SerpAPI/Tavily if their env vars
    # are set. An empty list is fine — the engine just falls back to
    # pure-Ollama generation (the original Phase K behavior).
    _emit({"stage": "web_search", "query": topic_clean})
    web_hits: list[SearchHit] = []
    try:
        web_hits = web_search(topic_clean, max_results=_WEB_HITS[depth])
    except Exception as e:  # noqa: BLE001
        log.warning("Web search failed (%s); proceeding without grounding", e)
    web_context = format_hits_for_prompt(web_hits)
    _emit({"stage": "web_search_done", "hits": len(web_hits)})

    # ── 1. Outline ──────────────────────────────────────────────────────
    _emit({"stage": "outline"})
    outline_user = _outline_user(topic_clean, depth)
    if web_context:
        outline_user = f"{web_context}\n\n---\n\n{outline_user}"
    try:
        outline_raw = _post_ollama(
            system=_OUTLINE_SYSTEM,
            user=outline_user,
            model=model,
            ollama_url=ollama_url,
            expect_json=True,
            temperature=0.4,
        )
        sections = _parse_outline(outline_raw)
    except Exception as e:  # noqa: BLE001
        log.warning("Outline call failed (%s); using fallback outline", e)
        sections = _fallback_outline()

    word_target = _WORD_TARGETS[depth]
    section_bodies: list[tuple[str, str]] = []
    total = len(sections)

    # ── 2. Sections ─────────────────────────────────────────────────────
    prior_titles: list[str] = []
    for i, title in enumerate(sections):
        _emit({"stage": "section_start", "title": title, "index": i, "total": total})
        section_user = _section_user(topic_clean, title, prior_titles)
        if web_context:
            section_user = f"{web_context}\n\n---\n\n{section_user}"
        try:
            body = _post_ollama(
                system=_section_system(word_target),
                user=section_user,
                model=model,
                ollama_url=ollama_url,
                expect_json=False,
                temperature=0.55,
            )
            body = _strip_leading_heading(body, title)
        except Exception as e:  # noqa: BLE001
            log.warning("Section %r failed: %s", title, e)
            body = f"_(section failed: {type(e).__name__}: {e})_"
        section_bodies.append((title, body))
        prior_titles.append(title)
        _emit(
            {
                "stage": "section_done",
                "title": title,
                "index": i + 1,
                "total": total,
            }
        )

    # ── 3. Self-critique (deep only) ────────────────────────────────────
    open_questions: str | None = None
    if depth == "deep":
        _emit({"stage": "critique"})
        draft_for_critique = "\n\n".join(
            f"## {t}\n\n{b}" for t, b in section_bodies
        )
        try:
            open_questions = _post_ollama(
                system=_CRITIQUE_SYSTEM,
                user=_critique_user(topic_clean, draft_for_critique),
                model=model,
                ollama_url=ollama_url,
                expect_json=False,
                temperature=0.4,
            ).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("Critique call failed: %s", e)
            open_questions = f"_(critique failed: {type(e).__name__}: {e})_"

    # ── 4. Comparison table ─────────────────────────────────────────────
    _emit({"stage": "comparison"})
    try:
        comparison_table = _post_ollama(
            system=_COMPARISON_SYSTEM,
            user=_comparison_user(topic_clean),
            model=model,
            ollama_url=ollama_url,
            expect_json=False,
            temperature=0.4,
        ).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("Comparison call failed: %s", e)
        comparison_table = f"_(comparison failed: {type(e).__name__}: {e})_"

    # ── 5. Compose ──────────────────────────────────────────────────────
    _emit({"stage": "compose"})
    now = datetime.now(UTC)
    title = topic_clean if len(topic_clean) < 120 else topic_clean[:117] + "..."
    tags = _derive_tags(topic_clean, depth)
    frontmatter = (
        "---\n"
        f"title: {_yaml_str(title)}\n"
        f"topic: {_yaml_str(topic_clean)}\n"
        f"depth: {depth}\n"
        f"generated_at: {now.isoformat()}\n"
        f"model: {_yaml_str(model)}\n"
        f"tags: [{', '.join(_yaml_str(t) for t in tags)}]\n"
        "---\n\n"
    )

    parts: list[str] = [f"# {title}\n"]
    for t, body in section_bodies:
        parts.append(f"## {t}\n\n{body.strip()}\n")
    if open_questions is not None:
        parts.append(f"## Open questions\n\n{open_questions}\n")
    parts.append(f"## Comparison table\n\n{comparison_table}\n")
    # K.2 — append the Sources section so readers can verify cited facts.
    sources_md = format_hits_for_sources_section(web_hits)
    if sources_md:
        parts.append(f"{sources_md}\n")

    return frontmatter + "\n".join(parts)


# ─── Misc ────────────────────────────────────────────────────────────────


def _yaml_str(s: str) -> str:
    """Conservatively quote a string for our trivial YAML frontmatter.

    We don't pull in PyYAML — the reader (router) uses a tiny hand-rolled
    parser. Strings with quotes or backslashes get escaped; everything else
    is wrapped in double quotes.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _derive_tags(topic: str, depth: Depth) -> list[str]:
    """Pull a handful of single-word lowercase tags out of the topic + depth.

    Best-effort — drops stopwords + numbers + duplicates. The frontmatter is
    only used for the rail list display; nothing depends on tag content.
    """
    stop = {
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at",
        "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
        "vs", "versus",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", topic.lower())
    tags: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in stop or w in seen:
            continue
        seen.add(w)
        tags.append(w)
        if len(tags) >= 5:
            break
    tags.append(depth)
    return tags
