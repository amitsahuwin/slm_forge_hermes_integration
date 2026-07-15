"""Universal dataset ingestion / converter.

Detects the format of an uploaded file, parses recognized formats directly,
and falls back to Ollama (Hermes bridge) when the structure is unknown or
too sparse. Produces chat-style records suitable for mlx_lm.lora training
and writes the three-way train/valid/canary split to disk.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("dataset_ingest.converter")

# ─────────────────────────────────────────────────────────────
#   Format detection
# ─────────────────────────────────────────────────────────────

KnownFormat = str  # one of the literals below
FORMATS = (
    "jsonl_chat",
    "jsonl_text",
    "jsonl_pc",
    "json_array",
    "csv",
    "plain_text",
    "markdown",
    "unknown",
)


def detect_file_format(filename: str, content_head: bytes) -> str:
    """Best-effort format sniff using filename + the first ~64 KB of bytes."""
    name = (filename or "").lower().strip()
    try:
        head = content_head.decode("utf-8", errors="replace").lstrip()
    except Exception:  # noqa: BLE001
        head = ""

    # Try JSONL first — one valid JSON object per line
    if name.endswith((".jsonl", ".ndjson")) or _looks_like_jsonl(head):
        sub = _classify_jsonl(head)
        if sub:
            return sub
        # Fall through to plain_text if jsonl tag but unparseable
        return "unknown"

    if name.endswith(".json") or head.startswith("[") or head.startswith("{"):
        # Could be a single array or a single chat record
        try:
            obj = json.loads(head if _looks_complete_json(head) else content_head.decode("utf-8", "replace"))
            if isinstance(obj, list):
                return "json_array"
            if isinstance(obj, dict) and "messages" in obj:
                return "jsonl_chat"
        except Exception:  # noqa: BLE001
            pass
        # Still might be JSON we couldn't fully parse from the head
        if head.startswith("["):
            return "json_array"

    if name.endswith(".csv") or name.endswith(".tsv"):
        return "csv"
    if "," in head.split("\n", 1)[0] and _looks_like_csv(head):
        return "csv"

    if name.endswith((".md", ".markdown")):
        return "markdown"
    if name.endswith((".txt", ".text")):
        return "plain_text"

    # Heuristic: if it's mostly printable ASCII/UTF-8 prose, treat as plain text
    if head and _is_mostly_text(content_head):
        return "markdown" if _has_md_markers(head) else "plain_text"

    return "unknown"


def _looks_like_jsonl(head: str) -> bool:
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if len(lines) < 1:
        return False
    ok = 0
    for ln in lines[:5]:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                ok += 1
        except Exception:  # noqa: BLE001
            return False
    return ok >= 1


def _classify_jsonl(head: str) -> str | None:
    """Return jsonl_chat / jsonl_text / jsonl_pc based on the first record."""
    for ln in head.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(obj, dict):
            return None
        if "messages" in obj and isinstance(obj["messages"], list):
            return "jsonl_chat"
        if "text" in obj:
            return "jsonl_text"
        if "prompt" in obj and ("completion" in obj or "response" in obj):
            return "jsonl_pc"
        if "instruction" in obj and ("response" in obj or "output" in obj):
            return "jsonl_pc"
        if "question" in obj and "answer" in obj:
            return "jsonl_pc"
        return "jsonl_text"  # something jsonl-ish but unknown schema
    return None


def _looks_complete_json(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    return (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}"))


def _looks_like_csv(head: str) -> bool:
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    cols = lines[0].count(",")
    if cols < 1:
        return False
    # At least 2 rows with similar comma counts
    matches = sum(1 for ln in lines[1:5] if abs(ln.count(",") - cols) <= 1)
    return matches >= 1


def _is_mostly_text(buf: bytes) -> bool:
    if not buf:
        return False
    try:
        s = buf.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(1 for c in s if c.isprintable() or c in "\n\r\t")
    return printable / max(len(s), 1) > 0.9


def _has_md_markers(head: str) -> bool:
    return any(
        marker in head
        for marker in ("# ", "## ", "```", "* ", "- ", "[", "**")
    )


# ─────────────────────────────────────────────────────────────
#   Direct parsing of recognized formats
# ─────────────────────────────────────────────────────────────


def parse_known(format_: str, content: bytes) -> list[dict]:
    """Parse a recognized format into a list of records."""
    text = content.decode("utf-8", errors="replace")

    if format_ == "jsonl_chat":
        return _parse_jsonl(text, expect_chat=True)
    if format_ in ("jsonl_text", "jsonl_pc"):
        return _parse_jsonl(text, expect_chat=False)
    if format_ == "json_array":
        try:
            arr = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"json_array: invalid JSON ({e})") from e
        if not isinstance(arr, list):
            raise ValueError("json_array: top-level value must be a list")
        return [r for r in arr if isinstance(r, dict)]
    if format_ == "csv":
        return _parse_csv(text)
    if format_ in ("markdown", "plain_text"):
        return _parse_paragraphs(text)

    raise ValueError(f"parse_known: unsupported format {format_!r}")


def _parse_jsonl(text: str, *, expect_chat: bool) -> list[dict]:
    out: list[dict] = []
    for i, ln in enumerate(text.splitlines(), start=1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as e:
            log.warning("jsonl line %d skipped: %s", i, e)
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# Column-name synonyms recognized as a prompt/completion pair. Shared by the
# synchronous converter and the constant-RAM streaming ingest path so both
# normalize CSV rows to identical MLX-trainable shapes.
_CSV_PROMPT_KEYS = ("prompt", "instruction", "question", "input", "user", "query")
_CSV_COMPLETION_KEYS = (
    "completion",
    "response",
    "answer",
    "output",
    "assistant",
    "reply",
)


def resolve_csv_mapping(
    fieldnames: list[str],
) -> tuple[str | None, str | None]:
    """Pick the (prompt, completion) columns from a CSV header, if any.

    Returns the first field whose lowercased name matches a known prompt /
    completion synonym, else ``None`` for that slot.
    """
    pkey = next((k for k in fieldnames if k.lower() in _CSV_PROMPT_KEYS), None)
    ckey = next((k for k in fieldnames if k.lower() in _CSV_COMPLETION_KEYS), None)
    return pkey, ckey


def csv_row_to_mlx(
    row: dict, pkey: str | None, ckey: str | None
) -> dict | None:
    """Map one CSV row (raw ``column -> value`` dict) to an MLX-trainable record.

    * A recognized prompt/completion pair collapses to ``{"prompt", "completion"}``
      (rows missing either half are skipped → ``None``).
    * Otherwise every non-empty column is concatenated into a single
      ``{"text": "col: val\\n..."}`` record.
    * A wholly empty row yields ``None``.
    """
    if pkey and ckey:
        p = str(row.get(pkey, "")).strip()
        c = str(row.get(ckey, "")).strip()
        if p and c:
            return {"prompt": p, "completion": c}
        return None
    parts = [f"{k}: {v}" for k, v in row.items() if str(v).strip()]
    if parts:
        return {"text": "\n".join(parts)}
    return None


def is_mlx_trainable(record: object) -> bool:
    """True if ``record`` matches an mlx_lm.lora training format.

    Supported: chat (``{"messages": [...]}``), completions
    (``{"prompt": ..., "completion": ...}``), and text (``{"text": ...}``).
    Anything else is rejected before publishing so the failure surfaces at
    ingest with a clear message rather than mid-training.
    """
    if not isinstance(record, dict):
        return False
    msgs = record.get("messages")
    if isinstance(msgs, list) and msgs:
        return True
    if "prompt" in record and "completion" in record:
        return True
    return isinstance(record.get("text"), str)


def _parse_csv(text: str) -> list[dict]:
    # Detect delimiter (comma vs tab)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    rows = [{(k or "").strip(): (v if v is not None else "") for k, v in r.items()} for r in reader]

    pkey, ckey = resolve_csv_mapping(fieldnames)
    out: list[dict] = []
    for r in rows:
        record = csv_row_to_mlx(r, pkey, ckey)
        if record is not None:
            out.append(record)
    return out


def _parse_paragraphs(text: str) -> list[dict]:
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [{"text": p} for p in paras]


# ─────────────────────────────────────────────────────────────
#   Ollama-driven conversion (fallback for messy text)
# ─────────────────────────────────────────────────────────────

_CHUNK_THRESHOLD = 4000
_CHUNK_SIZE = 2000

_BASE_OLLAMA_SYSTEM = (
    "Convert the following text into a JSONL dataset of chat-style records. "
    "Each record must be `{\"messages\": [{\"role\":\"user\",\"content\":\"...\"},"
    "{\"role\":\"assistant\",\"content\":\"...\"}]}`. "
    "Generate one record per logical conversational pair. "
    "Output JSONL only, no prose."
)


def _load_skill(name: str) -> str | None:
    """Best-effort load of a Hermes skill markdown by stem.

    Searches the project ``.hermes-skills/`` directory first (mounted into
    Docker), then the user's ``~/.hermes/skills/`` directory. Returns ``None``
    silently if the skill isn't found — callers should treat that as "skill
    not installed, use base prompt only".
    """
    from pathlib import Path

    candidates = [
        Path("/app/.hermes-skills") / f"{name}.md",
        Path(__file__).resolve().parents[2] / ".hermes-skills" / f"{name}.md",
        Path.home() / ".hermes" / "skills" / f"{name}.md",
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def _build_ollama_system() -> str:
    """Compose the system prompt for ``convert_via_ollama``.

    Appends the ``ingest_dataset`` Hermes skill (if installed) as
    supplementary schema-mapping context, so the model is more likely to
    produce records that match what SLM-Forge expects downstream. Falls
    back to the base prompt when the skill isn't present.
    """
    skill = _load_skill("ingest_dataset")
    if not skill:
        return _BASE_OLLAMA_SYSTEM
    return (
        _BASE_OLLAMA_SYSTEM
        + "\n\n--- Additional guidance from the `ingest_dataset` skill ---\n"
        + skill.strip()
        + "\n\nApply the schema-mapping heuristics above when picking which "
        "field becomes the user message and which becomes the assistant reply."
    )


# Computed at import — cheap I/O, single read.
_OLLAMA_SYSTEM = _build_ollama_system()


def convert_via_ollama(
    content: str,
    model: str,
    ollama_url: str,
    max_records: int | None = None,
    batch_size: int = 10,  # noqa: ARG001 (reserved for future streaming)
) -> list[dict]:
    """Ask Ollama to extract chat-style records from raw text.

    Chunks long inputs (>4000 chars) into ~2000-char windows and parses each
    response as JSONL. Invalid lines are skipped with a warning.
    """
    chunks = _chunk_text(content, _CHUNK_THRESHOLD, _CHUNK_SIZE)
    out: list[dict] = []
    for i, chunk in enumerate(chunks):
        log.info(
            "Ollama convert chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk)
        )
        try:
            raw = _call_ollama_jsonl(_OLLAMA_SYSTEM, chunk, model=model, url=ollama_url)
        except httpx.HTTPError as e:
            log.warning("Ollama call failed on chunk %d: %s", i + 1, e)
            continue
        out.extend(_parse_ollama_jsonl(raw))
        if max_records is not None and len(out) >= max_records:
            return out[:max_records]
    return out


def _chunk_text(content: str, threshold: int, size: int) -> list[str]:
    content = content.strip()
    if len(content) <= threshold:
        return [content] if content else []
    chunks: list[str] = []
    # Try paragraph-aware chunking first, fall back to flat slicing
    paragraphs = content.split("\n\n")
    buf = ""
    for p in paragraphs:
        if not p.strip():
            continue
        if len(buf) + len(p) + 2 > size and buf:
            chunks.append(buf.strip())
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf.strip():
        chunks.append(buf.strip())

    # Sanity fallback: if any chunk is still huge, slice it
    final: list[str] = []
    for c in chunks:
        if len(c) <= size * 2:
            final.append(c)
        else:
            for i in range(0, len(c), size):
                final.append(c[i : i + size])
    return final


def _call_ollama_jsonl(system: str, user: str, *, model: str, url: str) -> str:
    """Direct httpx call to Ollama /api/chat. format=json sometimes wraps the
    payload in a single object — we ask for plain text and parse JSONL ourselves
    so the model can emit multiple records.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    r = httpx.post(f"{url}/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _parse_ollama_jsonl(raw: str) -> list[dict]:
    out: list[dict] = []
    # Strip code-fence wrapping if present
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("```")
        )
    for ln in text.splitlines():
        ln = ln.strip().rstrip(",")
        if not ln or not (ln.startswith("{") and ln.endswith("}")):
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _valid_chat_record(obj):
            out.append(obj)
    return out


def _valid_chat_record(obj: dict) -> bool:
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 2:
        return False
    roles = {m.get("role") for m in msgs if isinstance(m, dict)}
    return "user" in roles and "assistant" in roles


# ─────────────────────────────────────────────────────────────
#   Splitting + writing
# ─────────────────────────────────────────────────────────────


def auto_split(
    records: list[dict],
    target_min_valid: int = 4,
    target_min_canary: int = 1,
    ratios: tuple[float, float, float] = (0.80, 0.15, 0.05),
) -> dict[str, list[dict]]:
    """Shuffle (seed=42) and split into train/valid/canary.

    Always tries to satisfy `valid >= target_min_valid` and
    `canary >= target_min_canary`. For tiny datasets (<6 records) we fall back
    to valid=1, canary=0 and log a warning.
    """
    if not records:
        return {"train": [], "valid": [], "canary": []}

    rng = random.Random(42)
    rec = list(records)
    rng.shuffle(rec)

    n = len(rec)
    if n < 6:
        log.warning(
            "auto_split: dataset has only %d records — using valid=1, canary=0", n
        )
        if n == 1:
            return {"train": rec, "valid": [], "canary": []}
        return {"train": rec[:-1], "valid": rec[-1:], "canary": []}

    r_train, r_valid, r_canary = ratios
    n_canary = max(target_min_canary, int(round(n * r_canary)))
    n_valid = max(target_min_valid, int(round(n * r_valid)))

    # Don't starve train: leave at least 1
    while n_valid + n_canary >= n and (n_valid > target_min_valid or n_canary > target_min_canary):
        if n_canary > target_min_canary:
            n_canary -= 1
        elif n_valid > target_min_valid:
            n_valid -= 1
        else:
            break

    if n_valid + n_canary >= n:
        n_valid = min(n_valid, max(1, n - n_canary - 1))

    n_train = n - n_valid - n_canary
    train = rec[:n_train]
    valid = rec[n_train : n_train + n_valid]
    canary = rec[n_train + n_valid :]
    return {"train": train, "valid": valid, "canary": canary}


def write_dataset(
    name: str,
    dataset_root: Path,
    splits: dict[str, list[dict]],
    source_format: str,
    source_filename: str,
    conversion_notes: str = "",
) -> Path:
    """Write train/valid/canary jsonl + README to `<dataset_root>/<name>/`."""
    dataset_dir = Path(dataset_root) / name
    dataset_dir.mkdir(parents=True, exist_ok=True)

    _write_jsonl(dataset_dir / "train.jsonl", splits.get("train", []))
    _write_jsonl(dataset_dir / "valid.jsonl", splits.get("valid", []))
    canary = splits.get("canary", [])
    if canary:
        _write_jsonl(dataset_dir / "canary.jsonl", canary)

    readme = dataset_dir / "README.md"
    ts = datetime.now(UTC).isoformat()
    body = (
        f"# {name}\n\n"
        f"Ingested via SLM-Forge universal converter on {ts}.\n\n"
        f"- **Source filename:** `{source_filename}`\n"
        f"- **Detected format:** `{source_format}`\n"
        f"- **Train rows:** {len(splits.get('train', []))}\n"
        f"- **Valid rows:** {len(splits.get('valid', []))}\n"
        f"- **Canary rows:** {len(canary)}\n"
    )
    if conversion_notes:
        body += f"\n## Conversion notes\n\n{conversion_notes}\n"
    readme.write_text(body, encoding="utf-8")
    return dataset_dir


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
