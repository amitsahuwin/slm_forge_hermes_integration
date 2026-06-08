"""Dataset synthesis engine — expand a small seed dataset via local Ollama.

Pure-Python, side-effect free. Callers do all file I/O; this module just
turns ``list[dict]`` examples into more ``list[dict]`` examples by asking
the local Hermes/Ollama model to generate records matching the same schema.

Format detection covers the three shapes the rest of SLM-Forge supports:

* ``chat``               → ``{"messages": [{"role": ..., "content": ...}, ...]}``
* ``prompt_completion``  → ``{"prompt": "...", "completion": "..."}``
* ``text``               → ``{"text": "..."}``  (chat-template-baked)

The contract:

* Pick 3-5 seed records as few-shot examples.
* Ask the model to emit JSONL — one JSON object per line, no prose or fences.
* Parse, validate schema-keys, dedup against seeds + prior generations.
* Loop until target_count reached OR safety cap hit.
* Report progress via callback after each batch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from collections.abc import Callable
from typing import Any, Literal

import httpx

from packages.ratchet.hermes_bridge import HERMES_MODEL, OLLAMA_URL

log = logging.getLogger("dataset_synth.engine")

DatasetFormat = Literal["chat", "text", "prompt_completion"]

ProgressEvent = dict[str, int]
ProgressCb = Callable[[ProgressEvent], None]


# ─── Format detection ────────────────────────────────────────────────────


def detect_format(records: list[dict]) -> DatasetFormat:
    """Detect the JSONL record shape from the first record's keys.

    Falls back to ``"text"`` for unknown shapes — the trainer treats any
    string-only record as text-format.
    """
    if not records:
        raise ValueError("Cannot detect format from empty record list")
    first = records[0]
    if not isinstance(first, dict):
        raise ValueError(f"Expected dict records, got {type(first).__name__}")
    keys = set(first.keys())
    if "messages" in keys:
        return "chat"
    if "prompt" in keys and "completion" in keys:
        return "prompt_completion"
    if "text" in keys:
        return "text"
    # Unknown — treat as text and let validation drop bad rows downstream.
    log.warning("Unknown record format with keys=%s; defaulting to 'text'", sorted(keys))
    return "text"


def _expected_keys(fmt: DatasetFormat) -> set[str]:
    if fmt == "chat":
        return {"messages"}
    if fmt == "prompt_completion":
        return {"prompt", "completion"}
    return {"text"}


def _validate_record(rec: Any, fmt: DatasetFormat) -> bool:
    """Return True if ``rec`` is structurally valid for ``fmt``."""
    if not isinstance(rec, dict):
        return False
    needed = _expected_keys(fmt)
    if not needed.issubset(rec.keys()):
        return False

    if fmt == "chat":
        msgs = rec.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return False
        for m in msgs:
            if not isinstance(m, dict):
                return False
            if not isinstance(m.get("role"), str):
                return False
            if not isinstance(m.get("content"), str):
                return False
        return True

    if fmt == "prompt_completion":
        return isinstance(rec.get("prompt"), str) and isinstance(rec.get("completion"), str)

    # text
    return isinstance(rec.get("text"), str) and bool(rec["text"].strip())


def _record_hash(rec: dict) -> str:
    """Stable hash of a record's canonical JSON form (sorted keys)."""
    payload = json.dumps(rec, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Prompt construction ─────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You are a data augmentation assistant. Your job is to generate MORE training "
    "records in EXACTLY the same JSON schema and similar style/domain as the "
    "examples provided.\n\n"
    "STRICT OUTPUT RULES:\n"
    "1. Output JSONL — one JSON object per line.\n"
    "2. Each line MUST be a single, parseable JSON object.\n"
    "3. NO prose, NO explanations, NO markdown code fences (```), NO commentary.\n"
    "4. The schema (the set of top-level keys) of every object MUST match the examples.\n"
    "5. Generate diverse but realistic content — vary the subject, tone within the "
    "domain, and specifics. Do NOT paraphrase the examples; invent NEW examples.\n"
    "6. If the examples include chat-template tokens like <|im_start|> / <|im_end|>, "
    "preserve them exactly.\n"
)


def _build_user_prompt(
    examples: list[dict],
    fmt: DatasetFormat,
    batch_size: int,
    style_guidance: str,
) -> str:
    schema_keys = sorted(_expected_keys(fmt))
    example_lines = "\n".join(
        json.dumps(ex, ensure_ascii=False) for ex in examples
    )
    parts = [
        f"FORMAT: {fmt}",
        f"REQUIRED TOP-LEVEL KEYS: {schema_keys}",
        "",
        "EXAMPLES (one JSON object per line):",
        example_lines,
        "",
    ]
    if style_guidance.strip():
        parts.append(f"STYLE GUIDANCE: {style_guidance.strip()}")
        parts.append("")
    parts.append(
        f"TASK: Generate {batch_size} MORE records in the same JSONL format. "
        f"One object per line. No prose, no fences. Begin immediately."
    )
    return "\n".join(parts)


# ─── Ollama call + parse ─────────────────────────────────────────────────


def _call_ollama_jsonl(
    system: str,
    user: str,
    *,
    model: str,
    ollama_url: str,
    temperature: float = 0.8,
    timeout: float = 600.0,
) -> str:
    """Call Ollama for free-form JSONL (NOT format=json, which would force one object)."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        r = httpx.post(f"{ollama_url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Ollama call failed: %s", e)
        raise
    return r.json()["message"]["content"]


def _strip_fences(text: str) -> str:
    """Remove leading/trailing markdown fences if the model ignored instructions."""
    lines = text.splitlines()
    # Drop opening ```jsonl / ```json / ``` lines
    while lines and lines[0].strip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines)


def _parse_jsonl(text: str) -> list[dict]:
    """Parse a JSONL chunk; skip blank lines and malformed JSON."""
    out: list[dict] = []
    for raw in _strip_fences(text).splitlines():
        s = raw.strip()
        if not s:
            continue
        # Some models prepend bullets or numbering; tolerate "1. {...}" / "- {...}"
        if s[0] in "-*0123456789" and "{" in s:
            s = s[s.index("{") :]
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
        elif isinstance(obj, list):
            # Model wrapped output in a list; flatten.
            for item in obj:
                if isinstance(item, dict):
                    out.append(item)
    return out


# ─── Main synthesis loop ─────────────────────────────────────────────────


def synthesize(
    source_records: list[dict],
    target_count: int,
    *,
    model: str = HERMES_MODEL,
    ollama_url: str = OLLAMA_URL,
    style_guidance: str = "",
    batch_size: int = 10,
    progress_cb: ProgressCb | None = None,
    max_iterations: int = 25,
    seed: int = 42,
) -> list[dict]:
    """Synthesize ``target_count`` new records modeled on ``source_records``.

    Side-effect free — caller writes results to disk. Calls ``progress_cb``
    after each batch with ``{"generated", "target", "batch", "dropped"}``.

    Returns the deduplicated list of synthesized records (NOT including
    source records — the caller decides whether to merge).
    """
    if not source_records:
        raise ValueError("source_records is empty — cannot bootstrap synthesis")
    if target_count <= 0:
        raise ValueError("target_count must be positive")

    fmt = detect_format(source_records)
    rng = random.Random(seed)

    # Build the seed-dedup set once.
    seen: set[str] = {_record_hash(r) for r in source_records if isinstance(r, dict)}
    generated: list[dict] = []

    batch_num = 0
    while len(generated) < target_count and batch_num < max_iterations:
        batch_num += 1
        remaining = target_count - len(generated)
        this_batch_target = min(batch_size, remaining)
        # Always ask for a couple extra to absorb dedup/validation losses.
        request_size = this_batch_target + 2

        # Pick 3-5 few-shot examples per call (re-sample each batch so the
        # model doesn't lock onto one slice of the seed set).
        k = min(len(source_records), rng.randint(3, 5))
        examples = rng.sample(source_records, k)

        user_prompt = _build_user_prompt(examples, fmt, request_size, style_guidance)

        try:
            raw = _call_ollama_jsonl(
                _SYSTEM_PROMPT,
                user_prompt,
                model=model,
                ollama_url=ollama_url,
            )
        except httpx.HTTPError as e:
            log.error("Synthesis batch %d failed: %s", batch_num, e)
            if progress_cb is not None:
                progress_cb(
                    {
                        "generated": len(generated),
                        "target": target_count,
                        "batch": batch_num,
                        "dropped": request_size,
                    }
                )
            continue

        candidates = _parse_jsonl(raw)
        dropped = 0
        for rec in candidates:
            if len(generated) >= target_count:
                break
            if not _validate_record(rec, fmt):
                dropped += 1
                continue
            h = _record_hash(rec)
            if h in seen:
                dropped += 1
                continue
            seen.add(h)
            generated.append(rec)

        log.info(
            "synth batch %d: parsed=%d kept=%d dropped=%d total=%d/%d",
            batch_num,
            len(candidates),
            len(candidates) - dropped,
            dropped,
            len(generated),
            target_count,
        )

        if progress_cb is not None:
            progress_cb(
                {
                    "generated": len(generated),
                    "target": target_count,
                    "batch": batch_num,
                    "dropped": dropped,
                }
            )

    if len(generated) < target_count:
        log.warning(
            "Synthesis stopped at %d/%d after %d batches (max_iterations cap)",
            len(generated),
            target_count,
            batch_num,
        )

    return generated
