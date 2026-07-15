"""Constant-RAM streaming primitives for the large-dataset ingest path.

The synchronous ingest path (``packages/dataset_ingest/converter.py``) reads
the whole file into memory, parses a ``list[dict]``, shuffles, and splits. That
is fine at 10 MB but blows up RAM at hundreds of MB. These primitives instead:

* parse JSONL / CSV **record-by-record** from an async byte-chunk stream,
  carrying only a small line buffer across chunk boundaries, and
* assign each record to train/valid/canary **once, in stream order**, writing
  it straight to disk — never holding the full record list.

The split rule (from the spec) guarantees ``valid >= 4`` and ``canary >= 1``
for any input with ``>= 5`` records and is deterministic for a given input
sequence: seed the minimums first, then assign by a stable per-record hash to
the ``0.80 / 0.15 / 0.05`` ratios. Because it does not shuffle, it cannot
reproduce ``auto_split``'s exact output — but it is reproducible and O(1) RAM,
which is what the large path needs.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import json
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import TextIO

from packages.dataset_ingest.converter import csv_row_to_mlx, resolve_csv_mapping

Record = dict[str, object]

# A parsed line is (record, dropped). Exactly one is meaningful:
#   valid record → (record, False)
#   bad line     → (None, True)
# Blank / whitespace-only lines are skipped entirely (not yielded).
ParsedLine = tuple[Record | None, bool]


async def iter_jsonl_records(
    chunks: AsyncIterable[bytes],
) -> AsyncIterator[ParsedLine]:
    """Yield ``(record | None, dropped)`` for each non-blank line of a JSONL stream.

    Splits on newlines across chunk boundaries. Lines that fail ``json.loads``
    or that parse to a non-object are counted as dropped, not raised.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    async for chunk in chunks:
        buf += decoder.decode(chunk)
        while True:
            nl = buf.find("\n")
            if nl == -1:
                break
            line = buf[:nl]
            buf = buf[nl + 1 :]
            parsed = _parse_jsonl_line(line)
            if parsed is not None:
                yield parsed
    buf += decoder.decode(b"", final=True)
    # Trailing line with no final newline.
    parsed = _parse_jsonl_line(buf)
    if parsed is not None:
        yield parsed


def _parse_jsonl_line(line: str) -> ParsedLine | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except (ValueError, json.JSONDecodeError):
        return (None, True)
    if not isinstance(obj, dict):
        return (None, True)
    return (obj, False)


async def iter_csv_records(
    chunks: AsyncIterable[bytes],
) -> AsyncIterator[ParsedLine]:
    """Yield ``(record | None, dropped)`` for each data row of a CSV stream.

    The first row is the header. Each data row is normalized to an
    MLX-trainable record (``{prompt, completion}`` for a recognized column
    pair, else ``{text}``) via the shared converter helpers, so the streaming
    path publishes the same shapes as the synchronous converter. Quoted fields
    containing commas/newlines are honoured. Constant RAM: the buffer is
    trimmed after every complete logical row, so it only ever holds the current
    (possibly quote-spanning) row. Empty / incomplete rows are skipped; rows
    whose field count differs from the header are counted as dropped.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    header: list[str] | None = None
    mapping: tuple[str | None, str | None] = (None, None)

    def _emit(row_text: str) -> ParsedLine | None:
        nonlocal header, mapping
        fields = _parse_csv_row(row_text)
        if fields is None:  # blank line
            return None
        if header is None:
            header = [h.strip() for h in fields]
            mapping = resolve_csv_mapping(header)
            return None
        if len(fields) != len(header):
            return (None, True)
        raw = dict(zip(header, fields, strict=True))
        record = csv_row_to_mlx(raw, *mapping)
        if record is None:  # empty row or incomplete prompt/completion pair
            return None
        return (record, False)

    async for chunk in chunks:
        buf += decoder.decode(chunk)
        while True:
            end = _find_row_end(buf)
            if end == -1:
                break
            row_text = buf[:end]
            buf = buf[end + 1 :]
            parsed = _emit(row_text)
            if parsed is not None:
                yield parsed
    buf += decoder.decode(b"", final=True)
    if buf:  # trailing row with no final newline
        parsed = _emit(buf)
        if parsed is not None:
            yield parsed


def _find_row_end(buf: str) -> int:
    """Index of the first ``\\n`` not inside a quoted field, else ``-1``.

    Quote parity is tracked from the start of ``buf``; since the buffer is
    trimmed to the remainder after each complete row, this is O(current row).
    """
    in_quotes = False
    for i, ch in enumerate(buf):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "\n" and not in_quotes:
            return i
    return -1


def _parse_csv_row(row_text: str) -> list[str] | None:
    """Parse a single logical CSV row into fields, or ``None`` if blank."""
    row_text = row_text.rstrip("\r")
    if not row_text.strip():
        return None
    for fields in csv.reader([row_text]):
        return fields
    return None


class StreamingSplitWriter:
    """Assign records to train/valid/canary once, in stream order, at O(1) RAM.

    Files are opened lazily and appended to as records arrive; nothing is held
    in memory. Assignment rule (per record):

    * if ``canary_count < target_min_canary`` → canary
    * elif ``valid_count < target_min_valid`` → valid
    * else assign by a stable per-record hash to the configured ratios.
    """

    def __init__(
        self,
        dataset_dir: Path,
        *,
        target_min_valid: int = 4,
        target_min_canary: int = 1,
        ratios: tuple[float, float, float] = (0.80, 0.15, 0.05),
    ) -> None:
        self._dir = Path(dataset_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._target_min_valid = target_min_valid
        self._target_min_canary = target_min_canary
        r_train, r_valid, _r_canary = ratios
        self._train_cut = r_train
        self._valid_cut = r_train + r_valid
        self._files: dict[str, TextIO] = {}
        self._counts = {"train": 0, "valid": 0, "canary": 0}
        self._total = 0

    def write(self, record: Record) -> None:
        split = self._assign(record)
        self._file(split).write(json.dumps(record, ensure_ascii=False) + "\n")
        self._counts[split] += 1
        self._total += 1

    def _assign(self, record: Record) -> str:
        if self._counts["canary"] < self._target_min_canary:
            return "canary"
        if self._counts["valid"] < self._target_min_valid:
            return "valid"
        frac = _stable_fraction(record)
        if frac < self._train_cut:
            return "train"
        if frac < self._valid_cut:
            return "valid"
        return "canary"

    def _file(self, split: str) -> TextIO:
        fh = self._files.get(split)
        if fh is None:
            fh = (self._dir / f"{split}.jsonl").open("w", encoding="utf-8")
            self._files[split] = fh
        return fh

    def finalize(self) -> dict[str, int]:
        for fh in self._files.values():
            fh.close()
        self._files.clear()
        return {
            "train": self._counts["train"],
            "valid": self._counts["valid"],
            "canary": self._counts["canary"],
            "records_total": self._total,
        }


def _stable_fraction(record: Record) -> float:
    """Map a record to a stable, uniform fraction in ``[0, 1)``.

    Deterministic across processes (unlike ``hash()``) — a canonical JSON
    encoding hashed with SHA-256, first 8 bytes scaled to ``[0, 1)``.
    """
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)
