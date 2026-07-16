"""Ingest-time CSV cleaning + chat-format conversion.

Spec: docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md. Shared by the sync ingest
paths (``converter.csv_to_chat``) and the streaming large-file path
(``streaming.iter_csv_chat_records``): tiered column mapping (heuristic →
Hermes → fail), per-reason row cleaning, and the >50% drop threshold.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

HermesResolver = Callable[[list[str], list[dict]], dict]

_PROMPT_HINTS = (
    "prompt",
    "instruction",
    "question",
    "input",
    "user",
    "query",
    "issue",
    "problem",
    "desc",
)
_COMPLETION_HINTS = (
    "completion",
    "response",
    "answer",
    "output",
    "assistant",
    "reply",
    "fix",
    "solution",
    "resolution",
)

_MIN_FIELD_CHARS = 3
_LIST_REPR_RE = re.compile(r"\[\s*(['\"]).*\1\s*\]", re.S)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SAMPLE_CAP = 5
_ACTIONABLE = "start Ollama or rename the columns to prompt/completion"


class MappingError(ValueError):
    """CSV columns could not be mapped to a (prompt, completion) pair."""


class DropThresholdError(ValueError):
    """More than the allowed ratio of data rows were dropped as unusable."""


@dataclass(frozen=True)
class ColumnMapping:
    prompt_col: str
    completion_col: str
    method: Literal["heuristic", "hermes"]

    def as_dict(self) -> dict[str, str]:
        return {
            "prompt_column": self.prompt_col,
            "completion_column": self.completion_col,
            "method": self.method,
        }


def _normalize_name(name: str) -> str:
    return name.lstrip("﻿").lower().replace("_", "").replace(" ", "")


def resolve_mapping(
    header: list[str],
    samples: list[dict],
    *,
    hermes_resolver: HermesResolver | None = None,
) -> ColumnMapping:
    columns = [h for h in header if h and h.strip()]
    if len(columns) < 2:
        raise MappingError(
            f"CSV needs at least two columns to map to prompt/completion (header: {header}); "
            + _ACTIONABLE
        )
    samples = samples[:_SAMPLE_CAP]

    def avg_len(col: str) -> float:
        values = [str(s.get(col) or "") for s in samples]
        return sum(len(v) for v in values) / max(len(values), 1)

    prompt_cands = [c for c in columns if any(h in _normalize_name(c) for h in _PROMPT_HINTS)]
    completion_cands = [
        c for c in columns if any(h in _normalize_name(c) for h in _COMPLETION_HINTS)
    ]
    if prompt_cands and completion_cands:
        prompt_col = max(prompt_cands, key=avg_len)
        completion_col = max(completion_cands, key=avg_len)
        if prompt_col != completion_col:
            return ColumnMapping(prompt_col, completion_col, "heuristic")

    if hermes_resolver is None:
        raise MappingError(
            f"Could not identify prompt/completion columns in header {columns}; " + _ACTIONABLE
        )
    try:
        result = hermes_resolver(list(header), samples)
    except Exception as exc:
        raise MappingError(f"Hermes column mapping failed ({exc}); " + _ACTIONABLE) from exc

    prompt_col = result.get("prompt_column") if isinstance(result, dict) else None
    completion_col = result.get("completion_column") if isinstance(result, dict) else None
    if (
        not isinstance(prompt_col, str)
        or not isinstance(completion_col, str)
        or prompt_col not in header
        or completion_col not in header
        or prompt_col == completion_col
    ):
        raise MappingError(
            f"Hermes returned an unusable column mapping {result!r}; " + _ACTIONABLE
        )
    return ColumnMapping(prompt_col, completion_col, "hermes")


@dataclass
class CleanStats:
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def count_drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def total_dropped(self) -> int:
        return sum(self.dropped.values())

    def check_threshold(self, max_ratio: float = 0.5) -> None:
        total = self.kept + self.total_dropped()
        if total and self.total_dropped() / total > max_ratio:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(self.dropped.items()))
            raise DropThresholdError(
                f"{self.total_dropped()} of {total} data rows "
                f"({self.total_dropped() / total:.0%}) are unusable ({reasons}); "
                "refusing to publish. Fix the source CSV and re-upload."
            )

    def readme_lines(self) -> list[str]:
        return [f"- Dropped {n} row(s): {reason}" for reason, n in sorted(self.dropped.items())]

    def warnings(self) -> list[str]:
        total = self.total_dropped()
        if not total:
            return []
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(self.dropped.items()))
        return [f"{total} row(s) dropped during CSV cleaning ({reasons})"]


def _is_list_repr(value: str) -> bool:
    if not _LIST_REPR_RE.fullmatch(value):
        return False
    try:
        return isinstance(ast.literal_eval(value), list)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False


class RowCleaner:
    """Stateful per-ingest cleaner: drop rules + dedupe + per-reason counts."""

    def __init__(self, mapping: ColumnMapping) -> None:
        self._mapping = mapping
        self._stats = CleanStats()
        self._seen: set[bytes] = set()

    @property
    def stats(self) -> CleanStats:
        return self._stats

    def clean(self, row: dict) -> dict | None:
        prompt = str(row.get(self._mapping.prompt_col) or "").strip()
        completion = str(row.get(self._mapping.completion_col) or "").strip()

        if len(prompt) < _MIN_FIELD_CHARS or len(completion) < _MIN_FIELD_CHARS:
            self._stats.count_drop("empty")
            return None
        if _is_list_repr(prompt) or _is_list_repr(completion):
            self._stats.count_drop("list_repr")
            return None
        if _CONTROL_CHARS_RE.search(prompt) or _CONTROL_CHARS_RE.search(completion):
            self._stats.count_drop("control_chars")
            return None

        normalized = "\x1f".join(" ".join(v.split()).casefold() for v in (prompt, completion))
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()[:16]
        if digest in self._seen:
            self._stats.count_drop("duplicate")
            return None
        self._seen.add(digest)

        self._stats.kept += 1
        return {
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": completion},
            ]
        }


def default_hermes_resolver(header: list[str], samples: list[dict]) -> dict:
    """Resolve an ambiguous CSV header via the ``csv_column_mapping`` Hermes skill.

    Raises on any Ollama/parse failure — ``resolve_mapping`` wraps it into an
    actionable :class:`MappingError`. No silent fallback.
    """
    from packages.ratchet.hermes_bridge import run_skill

    raw = run_skill(
        "csv_column_mapping",
        {"header": header, "sample_rows": samples[:_SAMPLE_CAP]},
        expect_json=True,
    )
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"skill returned {type(parsed).__name__}, expected object")
    return parsed