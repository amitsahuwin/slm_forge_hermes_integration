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

import asyncio
import codecs
import csv
import hashlib
import json
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import TextIO

from packages.dataset_ingest import csv_chat
from packages.dataset_ingest.csv_chat import RowCleaner

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


async def iter_csv_chat_records(
    chunks: AsyncIterable[bytes],
    *,
    hermes_resolver: csv_chat.HermesResolver | None = None,
    sample_size: int = 5,
    buffer_max: int = 50,
    meta: dict | None = None,
) -> AsyncIterator[ParsedLine]:
    """Yield ``(chat_record | None, dropped)`` for each data row of a CSV stream.

    Spec: docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md. The first row is the
    header. Up to ``buffer_max`` raw rows are buffered so the column mapping
    can be resolved once (heuristic → Hermes, which needs header + sample
    rows up front); the buffer is then flushed in order and every following
    row streams straight through the shared :class:`RowCleaner`. RAM stays
    bounded: a fixed-size row buffer plus 16-byte dedupe digests (~16 MB per
    million rows). Quoted fields containing commas/newlines are honoured.
    Blank rows are skipped; field-count mismatches and cleaner rejections are
    counted as dropped. ``MappingError`` is raised when no mapping exists.

    If ``meta`` is given, ``meta["mapping"]`` / ``meta["stats"]`` are set at
    resolution time so callers can report the mapping and per-reason drops.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    header: list[str] | None = None
    cleaner: RowCleaner | None = None
    # None marks a field-count-vs-header mismatch buffered before resolution.
    pending: list[dict | None] = []

    def _resolve() -> RowCleaner:
        samples = [r for r in pending if r is not None][:sample_size]
        resolver = (
            hermes_resolver
            if hermes_resolver is not None
            else csv_chat.default_hermes_resolver
        )
        mapping = csv_chat.resolve_mapping(header or [], samples, hermes_resolver=resolver)
        resolved = RowCleaner(mapping)
        if meta is not None:
            meta["mapping"] = mapping
            meta["stats"] = resolved.stats
        return resolved

    def _clean(active: RowCleaner, row: dict | None) -> ParsedLine:
        if row is None:
            active.stats.count_drop("field_mismatch")
            return (None, True)
        record = active.clean(row)
        if record is None:
            return (None, True)
        return (record, False)

    def _to_row(row_text: str) -> dict | None | tuple[()]:
        """Parse one logical row → dict, None (mismatch), or () for skip."""
        nonlocal header
        fields = _parse_csv_row(row_text)
        if fields is None or not any(f.strip() for f in fields):  # blank
            return ()
        if header is None:
            header = [h.strip() for h in fields]
            return ()
        if len(fields) != len(header):
            return None
        return dict(zip(header, fields, strict=True))

    async def _process(row_text: str) -> list[ParsedLine]:
        nonlocal cleaner
        row = _to_row(row_text)
        if row == ():
            return []
        assert not isinstance(row, tuple)
        if cleaner is not None:
            return [_clean(cleaner, row)]
        pending.append(row)
        if len(pending) < buffer_max:
            return []
        cleaner = await asyncio.to_thread(_resolve)  # Hermes call off the loop
        flushed = [_clean(cleaner, buffered) for buffered in pending]
        pending.clear()
        return flushed

    async for chunk in chunks:
        buf += decoder.decode(chunk)
        while True:
            end = _find_row_end(buf)
            if end == -1:
                break
            row_text = buf[:end]
            buf = buf[end + 1 :]
            for parsed in await _process(row_text):
                yield parsed
    buf += decoder.decode(b"", final=True)
    if buf:  # trailing row with no final newline
        for parsed in await _process(buf):
            yield parsed

    if cleaner is None:  # EOF before the buffer filled: resolve now
        cleaner = await asyncio.to_thread(_resolve)
        for buffered in pending:
            yield _clean(cleaner, buffered)
        pending.clear()


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
