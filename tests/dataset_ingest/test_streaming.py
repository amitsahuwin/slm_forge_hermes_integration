"""Constant-RAM streaming parse + split primitives (large-dataset-upload Step 2).

Pure functions / small class — heaviest unit coverage in the feature. No I/O
beyond a tmp dir for the split writer; no network, no DB.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from packages.dataset_ingest.csv_chat import MappingError
from packages.dataset_ingest.streaming import (
    StreamingSplitWriter,
    iter_csv_chat_records,
    iter_jsonl_records,
)


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


async def _collect(source):  # type: ignore[no-untyped-def]
    out = []
    async for item in source:
        out.append(item)
    return out


# ─────────────────────────── JSONL parsing ───────────────────────────


@pytest.mark.asyncio
async def test_jsonl_basic_records() -> None:
    data = b'{"a":1}\n{"a":2}\n{"a":3}\n'
    got = await _collect(iter_jsonl_records(_chunks(data, 4)))
    records = [r for r, dropped in got if not dropped]
    assert records == [{"a": 1}, {"a": 2}, {"a": 3}]
    assert all(dropped is False for _, dropped in got)


@pytest.mark.asyncio
async def test_jsonl_split_across_chunk_boundaries() -> None:
    data = b'{"key":"value","n":123}\n{"key":"other","n":456}\n'
    # 1-byte chunks — every record spans many boundaries.
    got = await _collect(iter_jsonl_records(_chunks(data, 1)))
    records = [r for r, dropped in got if not dropped]
    assert records == [
        {"key": "value", "n": 123},
        {"key": "other", "n": 456},
    ]


@pytest.mark.asyncio
async def test_jsonl_blank_lines_skipped() -> None:
    data = b'{"a":1}\n\n   \n{"a":2}\n'
    got = await _collect(iter_jsonl_records(_chunks(data, 8)))
    # Blank / whitespace-only lines are neither records nor drops.
    assert got == [({"a": 1}, False), ({"a": 2}, False)]


@pytest.mark.asyncio
async def test_jsonl_bad_lines_counted_not_raised() -> None:
    data = b'{"a":1}\nnot json\n{"a":2}\n{bad}\n'
    got = await _collect(iter_jsonl_records(_chunks(data, 5)))
    records = [r for r, dropped in got if not dropped]
    drops = [1 for _, dropped in got if dropped]
    assert records == [{"a": 1}, {"a": 2}]
    assert sum(drops) == 2


@pytest.mark.asyncio
async def test_jsonl_no_trailing_newline() -> None:
    data = b'{"a":1}\n{"a":2}'  # last line has no \n
    got = await _collect(iter_jsonl_records(_chunks(data, 3)))
    records = [r for r, dropped in got if not dropped]
    assert records == [{"a": 1}, {"a": 2}]


@pytest.mark.asyncio
async def test_jsonl_multibyte_char_split_across_chunk() -> None:
    # "café" and an emoji — multi-byte UTF-8 sequences that will be split
    # mid-codepoint by 1-byte chunking. An incremental decoder must not
    # corrupt them.
    data = json.dumps({"t": "café 🎉"}, ensure_ascii=False).encode("utf-8") + b"\n"
    got = await _collect(iter_jsonl_records(_chunks(data, 1)))
    records = [r for r, dropped in got if not dropped]
    assert records == [{"t": "café 🎉"}]


@pytest.mark.asyncio
async def test_jsonl_non_object_line_is_dropped() -> None:
    # A bare JSON array/scalar is valid JSON but not a training record.
    data = b'{"a":1}\n[1,2,3]\n"hello"\n'
    got = await _collect(iter_jsonl_records(_chunks(data, 6)))
    records = [r for r, dropped in got if not dropped]
    drops = sum(1 for _, dropped in got if dropped)
    assert records == [{"a": 1}]
    assert drops == 2


# ─────────────────────────── CSV → chat streaming ───────────────────────────


def _chat(p: str, c: str) -> dict:
    return {
        "messages": [
            {"role": "user", "content": p},
            {"role": "assistant", "content": c},
        ]
    }


@pytest.mark.asyncio
async def test_csv_chat_synonym_pair_maps_heuristically() -> None:
    data = b"instruction,response\nquestion one,answer one\nquestion two,answer two\n"
    got = await _collect(iter_csv_chat_records(_chunks(data, 5)))
    records = [r for r, dropped in got if not dropped]
    assert records == [
        _chat("question one", "answer one"),
        _chat("question two", "answer two"),
    ]


@pytest.mark.asyncio
async def test_csv_chat_issue_fix_columns_map_heuristically() -> None:
    # Real-world regression (dataset `sx-100rows`): these columns previously
    # fell through to a `{text}` column dump and trained gibberish.
    data = (
        b"issue_description,fix_provided,priority_value\n"
        b"disk full on node,restarted the node,high\n"
    )
    got = await _collect(iter_csv_chat_records(_chunks(data, 7)))
    records = [r for r, dropped in got if not dropped]
    assert records == [_chat("disk full on node", "restarted the node")]


@pytest.mark.asyncio
async def test_csv_chat_quoted_fields_preserved_across_chunks() -> None:
    # Quoted commas/newlines chunked 1 byte at a time must survive intact in
    # the message contents (in-quote newline is not a row terminator).
    data = 'prompt,completion\n"café, one\nline two","answer text"\n'.encode()
    got = await _collect(iter_csv_chat_records(_chunks(data, 1)))
    records = [r for r, dropped in got if not dropped]
    assert records == [_chat("café, one\nline two", "answer text")]


@pytest.mark.asyncio
async def test_csv_chat_empty_rows_skipped_not_dropped() -> None:
    data = b"prompt,completion\nrow one,fix one\n\nrow two,fix two\n"
    got = await _collect(iter_csv_chat_records(_chunks(data, 4)))
    assert [r for r, dropped in got if not dropped] == [
        _chat("row one", "fix one"),
        _chat("row two", "fix two"),
    ]
    assert sum(1 for _, dropped in got if dropped) == 0


@pytest.mark.asyncio
async def test_csv_chat_ambiguous_headers_use_resolver_once() -> None:
    rows = "".join(f'"question number {i}","answer number {i}"\n' for i in range(60))
    data = ("col_a,col_b\n" + rows).encode()
    calls: list[list[str]] = []

    def resolver(header: list[str], samples: list[dict]) -> dict:
        calls.append(header)
        assert 0 < len(samples) <= 5
        return {"prompt_column": "col_a", "completion_column": "col_b"}

    got = await _collect(iter_csv_chat_records(_chunks(data, 64), hermes_resolver=resolver))
    records = [r for r, dropped in got if not dropped]
    assert calls == [["col_a", "col_b"]]
    assert len(records) == 60  # buffered rows flushed in order, then streamed
    assert records[0] == _chat("question number 0", "answer number 0")
    assert records[-1] == _chat("question number 59", "answer number 59")


@pytest.mark.asyncio
async def test_csv_chat_small_file_resolves_at_eof() -> None:
    # Fewer data rows than the mapping buffer: resolution happens at EOF.
    data = b'col_a,col_b\n"tiny question here","tiny answer here"\n'

    def resolver(header: list[str], samples: list[dict]) -> dict:
        return {"prompt_column": "col_a", "completion_column": "col_b"}

    got = await _collect(iter_csv_chat_records(_chunks(data, 8), hermes_resolver=resolver))
    assert [r for r, dropped in got if not dropped] == [
        _chat("tiny question here", "tiny answer here")
    ]


@pytest.mark.asyncio
async def test_csv_chat_resolver_failure_raises_mapping_error() -> None:
    data = b'col_a,col_b\n"some question here","some answer here"\n'

    def resolver(header: list[str], samples: list[dict]) -> dict:
        raise ConnectionError("Ollama is down")

    with pytest.raises(MappingError):
        await _collect(iter_csv_chat_records(_chunks(data, 8), hermes_resolver=resolver))


@pytest.mark.asyncio
async def test_csv_chat_bad_rows_counted_as_dropped() -> None:
    data = (
        b"prompt,completion\n"
        b"good question one,good answer one\n"
        b"missing answer row,\n"
        b'list repr row,"[\'TRUE\', \'P4\']"\n'
        b"good question one,good answer one\n"
        b"too,many,fields,here\n"
        b"good question two,good answer two\n"
    )
    got = await _collect(iter_csv_chat_records(_chunks(data, 16)))
    records = [r for r, dropped in got if not dropped]
    drops = sum(1 for _, dropped in got if dropped)
    assert records == [
        _chat("good question one", "good answer one"),
        _chat("good question two", "good answer two"),
    ]
    assert drops == 4  # empty, list_repr, duplicate, field mismatch


# ─────────────────────────── Split writer ───────────────────────────


def _read_split(dataset_dir: Path, name: str) -> list[dict]:
    path = dataset_dir / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_split_writer_guarantees_minimums_for_n5(tmp_path: Path) -> None:
    w = StreamingSplitWriter(tmp_path)
    for i in range(5):
        w.write({"id": i})
    counts = w.finalize()
    assert counts["valid"] >= 4
    assert counts["canary"] >= 1
    assert counts["records_total"] == 5


def test_split_writer_deterministic(tmp_path: Path) -> None:
    records = [{"id": i, "text": f"row-{i}"} for i in range(200)]

    d1 = tmp_path / "a"
    w1 = StreamingSplitWriter(d1)
    for r in records:
        w1.write(r)
    c1 = w1.finalize()

    d2 = tmp_path / "b"
    w2 = StreamingSplitWriter(d2)
    for r in records:
        w2.write(r)
    c2 = w2.finalize()

    assert c1 == c2
    assert _read_split(d1, "train") == _read_split(d2, "train")
    assert _read_split(d1, "valid") == _read_split(d2, "valid")
    assert _read_split(d1, "canary") == _read_split(d2, "canary")


def test_split_writer_ratio_distribution_large_n(tmp_path: Path) -> None:
    n = 10_000
    w = StreamingSplitWriter(tmp_path)
    for i in range(n):
        w.write({"id": i})
    c = w.finalize()
    assert c["records_total"] == n
    assert c["train"] + c["valid"] + c["canary"] == n
    # Hash-ratio 0.80/0.15/0.05 — generous tolerance for a finite sample.
    assert 0.74 < c["train"] / n < 0.86
    assert 0.11 < c["valid"] / n < 0.19
    assert 0.02 < c["canary"] / n < 0.09


def test_split_writer_records_total_below_5(tmp_path: Path) -> None:
    w = StreamingSplitWriter(tmp_path)
    for i in range(3):
        w.write({"id": i})
    c = w.finalize()
    # The writer reports the total; the caller enforces the >=5 precondition.
    assert c["records_total"] == 3


def test_split_writer_all_records_preserved(tmp_path: Path) -> None:
    records = [{"id": i} for i in range(50)]
    w = StreamingSplitWriter(tmp_path)
    for r in records:
        w.write(r)
    w.finalize()
    combined = (
        _read_split(tmp_path, "train")
        + _read_split(tmp_path, "valid")
        + _read_split(tmp_path, "canary")
    )
    assert sorted(r["id"] for r in combined) == list(range(50))
