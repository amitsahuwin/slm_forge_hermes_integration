"""Parse uploaded files into row dicts. Supports JSONL and CSV."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator


def parse_jsonl(content: bytes) -> Iterator[dict]:
    """Yield one dict per non-empty line."""
    text = content.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: invalid JSON ({e})") from e


def parse_csv(content: bytes) -> Iterator[dict]:
    """Yield one dict per CSV row (first row used as headers)."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    for row in reader:
        yield {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }


def parse_json_array(content: bytes) -> Iterator[dict]:
    """For files that are a JSON array of objects, not JSONL."""
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("JSON file is not a top-level array")
    for item in data:
        if isinstance(item, dict):
            yield item


def detect_format(filename: str, content: bytes) -> str:
    """Return 'jsonl' | 'csv' | 'json' | 'unknown'."""
    name = filename.lower()
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".json"):
        # could be JSONL with .json extension OR JSON array — sniff
        head = content[:200].lstrip()
        if head.startswith(b"["):
            return "json"
        return "jsonl"
    # No extension — sniff
    head = content[:1024].decode("utf-8", errors="replace").strip()
    if head.startswith("[") and head.endswith("]"):
        return "json"
    if head.startswith("{"):
        return "jsonl"
    first_line = head.split("\n", 1)[0]
    if "," in first_line and "{" not in first_line:
        return "csv"
    return "unknown"


def parse_auto(filename: str, content: bytes) -> tuple[str, list[dict]]:
    """Parse a file and return (format, rows). Raises ValueError on unknown."""
    fmt = detect_format(filename, content)
    if fmt == "jsonl":
        return fmt, list(parse_jsonl(content))
    if fmt == "csv":
        return fmt, list(parse_csv(content))
    if fmt == "json":
        return fmt, list(parse_json_array(content))
    raise ValueError(
        f"Could not detect format of {filename!r}. "
        "Expected .jsonl, .csv, or .json (array of objects)."
    )
