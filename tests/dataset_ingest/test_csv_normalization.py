"""Shared CSV→MLX normalization + trainability helpers.

These helpers (``converter.py``) are the single source of truth for turning a
CSV row into an mlx_lm.lora-trainable record, used by BOTH the synchronous
converter (``_parse_csv`` / ``parse_known``) and the constant-RAM streaming
ingest path (``streaming.iter_csv_records``). The tests lock every branch of
that contract so the two paths cannot diverge again.
"""
from __future__ import annotations

from packages.dataset_ingest.converter import (
    csv_row_to_mlx,
    is_mlx_trainable,
    parse_known,
    resolve_csv_mapping,
)


# ─────────────────────────── resolve_csv_mapping ───────────────────────────


def test_resolve_mapping_exact_prompt_completion() -> None:
    assert resolve_csv_mapping(["prompt", "completion"]) == ("prompt", "completion")


def test_resolve_mapping_synonyms_case_insensitive() -> None:
    assert resolve_csv_mapping(["Instruction", "Response"]) == (
        "Instruction",
        "Response",
    )


def test_resolve_mapping_no_pair() -> None:
    assert resolve_csv_mapping(["issue_description", "fix_provided"]) == (None, None)


# ─────────────────────────── csv_row_to_mlx ───────────────────────────


def test_row_known_pair_complete() -> None:
    row = {"prompt": " hi ", "completion": " there "}
    assert csv_row_to_mlx(row, "prompt", "completion") == {
        "prompt": "hi",
        "completion": "there",
    }


def test_row_known_pair_incomplete_is_skipped() -> None:
    # Prompt present, completion blank → row dropped (returns None).
    assert csv_row_to_mlx({"prompt": "hi", "completion": ""}, "prompt", "completion") is None


def test_row_unknown_columns_become_text() -> None:
    row = {"issue": "disk full", "fix": "restart"}
    assert csv_row_to_mlx(row, None, None) == {"text": "issue: disk full\nfix: restart"}


def test_row_all_blank_is_skipped() -> None:
    assert csv_row_to_mlx({"a": "", "b": "   "}, None, None) is None


# ─────────────────────────── is_mlx_trainable ───────────────────────────


def test_trainable_chat_messages() -> None:
    assert is_mlx_trainable({"messages": [{"role": "user", "content": "x"}]}) is True


def test_trainable_prompt_completion() -> None:
    assert is_mlx_trainable({"prompt": "p", "completion": "c"}) is True


def test_trainable_text() -> None:
    assert is_mlx_trainable({"text": "hello"}) is True


def test_untrainable_custom_schema() -> None:
    assert is_mlx_trainable({"issue_description": "x", "fix": "y"}) is False


def test_untrainable_empty_messages_list() -> None:
    assert is_mlx_trainable({"messages": []}) is False


def test_untrainable_non_dict() -> None:
    assert is_mlx_trainable("not a dict") is False
    assert is_mlx_trainable([1, 2, 3]) is False


# ─────────────────────── sync _parse_csv (via parse_known) ───────────────────────


def test_parse_known_csv_unknown_columns_to_text() -> None:
    csv_bytes = b"issue_description,fix_provided\ndisk full,restart\n"
    assert parse_known("csv", csv_bytes) == [
        {"text": "issue_description: disk full\nfix_provided: restart"}
    ]


def test_parse_known_csv_known_pair_to_prompt_completion() -> None:
    csv_bytes = b"question,answer\nq1,a1\nq2,a2\n"
    assert parse_known("csv", csv_bytes) == [
        {"prompt": "q1", "completion": "a1"},
        {"prompt": "q2", "completion": "a2"},
    ]


def test_parse_known_csv_incomplete_pair_rows_dropped() -> None:
    csv_bytes = b"prompt,completion\nfull,ok\nmissing,\n"
    assert parse_known("csv", csv_bytes) == [{"prompt": "full", "completion": "ok"}]
