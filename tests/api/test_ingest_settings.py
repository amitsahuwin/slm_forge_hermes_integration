"""Tests for ingest upload-size configuration (large-dataset-upload Step 0).

Covers env parsing, defaults, and fail-fast validation for the two knobs:
``SLM_FORGE_MAX_UPLOAD_BYTES`` (async large-path cap) and
``SLM_FORGE_INGEST_SYNC_MAX_BYTES`` (sync-path threshold / frontend routing
boundary).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from apps.api.services.ingest_settings import (
    IngestSettings,
    get_ingest_settings,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    get_ingest_settings.cache_clear()
    yield
    get_ingest_settings.cache_clear()


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("SLM_FORGE_INGEST_SYNC_MAX_BYTES", raising=False)
    s = get_ingest_settings()
    assert s.max_upload_bytes == 524_288_000  # 500 MB
    assert s.sync_max_bytes == 10_485_760  # 10 MB


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_MAX_UPLOAD_BYTES", "1048576")
    monkeypatch.setenv("SLM_FORGE_INGEST_SYNC_MAX_BYTES", "2048")
    s = get_ingest_settings()
    assert s.max_upload_bytes == 1_048_576
    assert s.sync_max_bytes == 2048


def test_unparseable_max_upload_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_MAX_UPLOAD_BYTES", "not-a-number")
    with pytest.raises(ValueError):
        get_ingest_settings()


def test_zero_max_upload_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_MAX_UPLOAD_BYTES", "0")
    with pytest.raises(ValueError):
        get_ingest_settings()


def test_negative_sync_max_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_INGEST_SYNC_MAX_BYTES", "-1")
    with pytest.raises(ValueError):
        get_ingest_settings()


def test_sync_exceeding_max_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    # The sync threshold must not exceed the hard upload cap, otherwise the
    # routing boundary is nonsensical.
    monkeypatch.setenv("SLM_FORGE_MAX_UPLOAD_BYTES", "1000")
    monkeypatch.setenv("SLM_FORGE_INGEST_SYNC_MAX_BYTES", "2000")
    with pytest.raises(ValueError):
        get_ingest_settings()


def test_settings_is_frozen() -> None:
    s = IngestSettings(max_upload_bytes=100, sync_max_bytes=50)
    with pytest.raises(ValidationError):
        s.max_upload_bytes = 200  # type: ignore[misc]
