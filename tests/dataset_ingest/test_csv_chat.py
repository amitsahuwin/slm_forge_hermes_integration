"""Contract tests for ingest-time CSV cleaning + chat conversion.

Spec: docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md. These tests lock the tiered
column mapping (heuristic → Hermes → fail), every row-drop rule, the >50%
drop threshold, and the chat-format output shape shared by the sync and
streaming ingest paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from packages.dataset_ingest.csv_chat import (
    CleanStats,
    ColumnMapping,
    DropThresholdError,
    MappingError,
    RowCleaner,
    resolve_mapping,
)

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLES_PROSE = [
    {
        "issue_description": "disk full on /var on host02, utilization above 95 percent",
        "fix_provided": "cleaned rotated logs and extended the logical volume",
        "priority": "P3",
    },
    {
        "issue_description": "physical memory utilization above 80 percent on app03",
        "fix_provided": "restarted the leaking service, memory back to baseline",
        "priority": "P2",
    },
]


# ─────────────────────────── resolve_mapping: heuristic tier ───────────────────────────


def test_heuristic_maps_issue_fix_columns() -> None:
    m = resolve_mapping(["issue_description", "fix_provided", "priority"], SAMPLES_PROSE)
    assert m == ColumnMapping("issue_description", "fix_provided", "heuristic")


def test_heuristic_maps_exact_prompt_completion() -> None:
    samples = [{"prompt": "a long enough question about things", "completion": "a long enough answer about things"}]
    m = resolve_mapping(["prompt", "completion"], samples)
    assert (m.prompt_col, m.completion_col, m.method) == ("prompt", "completion", "heuristic")


def test_heuristic_normalizes_case_and_underscores() -> None:
    samples = [
        {"Issue Description": s["issue_description"], "Resolution_Notes": s["fix_provided"]}
        for s in SAMPLES_PROSE
    ]
    m = resolve_mapping(["Issue Description", "Resolution_Notes"], samples)
    assert (m.prompt_col, m.completion_col) == ("Issue Description", "Resolution_Notes")


def test_heuristic_prefers_text_heavy_candidate() -> None:
    # Two completion-ish columns; the prose-heavy one must win over the code-ish one.
    samples = [
        {
            "question": "why did the heartbeat alert fire on server01 this morning",
            "answer_code": "A1",
            "answer": "the collector lost connectivity; restarting the agent fixed it",
        }
    ] * 3
    m = resolve_mapping(["question", "answer_code", "answer"], samples)
    assert (m.prompt_col, m.completion_col) == ("question", "answer")


def test_heuristic_does_not_map_hintless_headers() -> None:
    resolver_called = False

    def resolver(header: list[str], samples: list[dict]) -> dict:
        nonlocal resolver_called
        resolver_called = True
        return {"prompt_column": "col_a", "completion_column": "col_b"}

    m = resolve_mapping(
        ["col_a", "col_b"],
        [{"col_a": s["issue_description"], "col_b": s["fix_provided"]} for s in SAMPLES_PROSE],
        hermes_resolver=resolver,
    )
    assert resolver_called
    assert m == ColumnMapping("col_a", "col_b", "hermes")


# ─────────────────────────── resolve_mapping: Hermes tier + failures ───────────────────────────


def test_ambiguous_without_resolver_raises() -> None:
    with pytest.raises(MappingError):
        resolve_mapping(["col_a", "col_b"], SAMPLES_PROSE)


def test_resolver_exception_surfaces_as_mapping_error() -> None:
    def resolver(header: list[str], samples: list[dict]) -> dict:
        raise ConnectionError("Ollama is down")

    with pytest.raises(MappingError, match="Ollama"):
        resolve_mapping(["col_a", "col_b"], SAMPLES_PROSE, hermes_resolver=resolver)


def test_resolver_unknown_column_raises() -> None:
    def resolver(header: list[str], samples: list[dict]) -> dict:
        return {"prompt_column": "nope", "completion_column": "col_b"}

    with pytest.raises(MappingError):
        resolve_mapping(["col_a", "col_b"], SAMPLES_PROSE, hermes_resolver=resolver)


def test_resolver_identical_columns_raises() -> None:
    def resolver(header: list[str], samples: list[dict]) -> dict:
        return {"prompt_column": "col_a", "completion_column": "col_a"}

    with pytest.raises(MappingError):
        resolve_mapping(["col_a", "col_b"], SAMPLES_PROSE, hermes_resolver=resolver)


def test_single_column_header_raises() -> None:
    with pytest.raises(MappingError):
        resolve_mapping(["only_col"], [{"only_col": "x"}])


# ─────────────────────────── RowCleaner drop rules ───────────────────────────

MAPPING = ColumnMapping("issue_description", "fix_provided", "heuristic")


def _clean_row(p: str = "a real issue description here", c: str = "a real fix description here") -> dict:
    return {"issue_description": p, "fix_provided": c, "priority": "P3"}


def test_clean_row_becomes_chat_record() -> None:
    cleaner = RowCleaner(MAPPING)
    rec = cleaner.clean(_clean_row("disk full on host", "extended the volume"))
    assert rec == {
        "messages": [
            {"role": "user", "content": "disk full on host"},
            {"role": "assistant", "content": "extended the volume"},
        ]
    }
    assert cleaner.stats.kept == 1
    assert cleaner.stats.total_dropped() == 0


def test_empty_completion_dropped() -> None:
    cleaner = RowCleaner(MAPPING)
    assert cleaner.clean(_clean_row(c="   ")) is None
    assert cleaner.stats.dropped["empty"] == 1


def test_too_short_field_dropped() -> None:
    cleaner = RowCleaner(MAPPING)
    assert cleaner.clean(_clean_row(p="ab")) is None
    assert cleaner.stats.dropped["empty"] == 1


def test_python_list_repr_dropped() -> None:
    cleaner = RowCleaner(MAPPING)
    row = _clean_row(c="['TRUE', 'Report an MTaaS issue', 'MTaaS Generic CI', 'P4']")
    assert cleaner.clean(row) is None
    assert cleaner.stats.dropped["list_repr"] == 1


def test_bracketed_prose_is_not_list_repr() -> None:
    cleaner = RowCleaner(MAPPING)
    rec = cleaner.clean(_clean_row(c="[resolved] restarted the agent, heartbeat fine"))
    assert rec is not None
    assert cleaner.stats.dropped.get("list_repr", 0) == 0


def test_control_chars_dropped() -> None:
    cleaner = RowCleaner(MAPPING)
    assert cleaner.clean(_clean_row(c="fix containing \x07 bell byte")) is None
    assert cleaner.stats.dropped["control_chars"] == 1


def test_newline_and_tab_are_allowed() -> None:
    cleaner = RowCleaner(MAPPING)
    assert cleaner.clean(_clean_row(c="step one\nstep two\tdone")) is not None


def test_exact_duplicates_dropped() -> None:
    cleaner = RowCleaner(MAPPING)
    assert cleaner.clean(_clean_row()) is not None
    assert cleaner.clean(_clean_row()) is None
    assert cleaner.stats.dropped["duplicate"] == 1
    assert cleaner.stats.kept == 1


# ─────────────────────────── CleanStats threshold + reporting ───────────────────────────


def test_threshold_over_half_raises() -> None:
    stats = CleanStats(kept=2, dropped={"empty": 3})
    with pytest.raises(DropThresholdError):
        stats.check_threshold()


def test_threshold_at_or_under_half_passes() -> None:
    CleanStats(kept=3, dropped={"empty": 3}).check_threshold()
    CleanStats(kept=5, dropped={}).check_threshold()


def test_readme_lines_and_warnings_report_reasons() -> None:
    stats = CleanStats(kept=8, dropped={"empty": 1, "list_repr": 2})
    joined = "\n".join(stats.readme_lines())
    assert "empty" in joined and "list_repr" in joined
    assert any("3" in w for w in stats.warnings())


def test_no_drops_produce_no_warnings() -> None:
    assert CleanStats(kept=5, dropped={}).warnings() == []


# ─────────────────────────── sync wrapper: converter.csv_to_chat ───────────────────────────


def test_csv_to_chat_on_corrupted_fixture() -> None:
    from packages.dataset_ingest.converter import csv_to_chat

    text = (FIXTURES / "corrupted_issues.csv").read_text(encoding="utf-8")
    records, mapping, stats = csv_to_chat(text)

    assert mapping == ColumnMapping("issue_description", "fix_provided", "heuristic")
    assert len(records) == 8  # 13 data rows: 8 clean, 5 bad
    assert all(set(r) == {"messages"} for r in records)
    assert stats.dropped["list_repr"] == 1
    assert stats.dropped["empty"] == 1
    assert stats.dropped["duplicate"] == 1
    assert stats.dropped["control_chars"] == 1
    assert stats.dropped["field_mismatch"] == 1


def test_csv_to_chat_multiline_cells_parse_intact() -> None:
    from packages.dataset_ingest.converter import csv_to_chat

    text = (FIXTURES / "clean_issues.csv").read_text(encoding="utf-8")
    records, _, stats = csv_to_chat(text)
    assert stats.total_dropped() == 0
    last = records[-1]["messages"]
    assert last[0]["content"] == "multi-line incident\nsecond line of the same cell"
    assert last[1]["content"] == "multi-line fix\napplied in two steps"


def test_csv_to_chat_garbage_majority_raises_threshold() -> None:
    from packages.dataset_ingest.converter import csv_to_chat

    text = (FIXTURES / "garbage_majority.csv").read_text(encoding="utf-8")
    with pytest.raises(DropThresholdError):
        csv_to_chat(text)


def test_csv_to_chat_ambiguous_uses_resolver() -> None:
    from packages.dataset_ingest.converter import csv_to_chat

    text = (FIXTURES / "ambiguous_headers.csv").read_text(encoding="utf-8")
    calls: list[list[str]] = []

    def resolver(header: list[str], samples: list[dict]) -> dict:
        calls.append(header)
        assert len(samples) <= 5
        return {"prompt_column": "col_a", "completion_column": "col_b"}

    records, mapping, _ = csv_to_chat(text, hermes_resolver=resolver)
    assert calls == [["col_a", "col_b"]]
    assert mapping.method == "hermes"
    assert len(records) == 8