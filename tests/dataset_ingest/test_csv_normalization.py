"""Trainability contract for records published by any ingest path.

CSV column mapping / cleaning moved to ``csv_chat`` (see
``test_csv_chat.py`` and docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md); what
remains here is the ``is_mlx_trainable`` gate that both the sync and
streaming paths apply before publishing a record.
"""
from __future__ import annotations

from packages.dataset_ingest.converter import is_mlx_trainable


def test_chat_record_is_trainable() -> None:
    assert is_mlx_trainable(
        {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}
    )


def test_prompt_completion_record_is_trainable() -> None:
    assert is_mlx_trainable({"prompt": "q", "completion": "a"})


def test_text_record_is_trainable() -> None:
    assert is_mlx_trainable({"text": "some prose"})


def test_empty_messages_list_is_not_trainable() -> None:
    assert not is_mlx_trainable({"messages": []})


def test_raw_column_dict_is_not_trainable() -> None:
    assert not is_mlx_trainable({"issue_description": "x", "fix_provided": "y"})


def test_non_dict_is_not_trainable() -> None:
    assert not is_mlx_trainable(["not", "a", "dict"])
    assert not is_mlx_trainable("text")
    assert not is_mlx_trainable(None)