"""PR-1 A4 — additive migration for ``hermes_traces``.

We simulate a pre-migration table (without ``tenant_id`` and ``attempts``
columns), run ``_migrate_hermes_traces()``, and assert the columns are
added with safe defaults. Repeating the call must be idempotent — a real
production restart will replay it on every boot.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from apps.api.services import db as db_module


@pytest.fixture()
def legacy_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a hermes_traces table WITHOUT the new columns, simulating a
    DB created before PR-1 A4 shipped."""
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE hermes_traces (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP,
                    source TEXT,
                    model TEXT,
                    request_body TEXT,
                    response_body TEXT,
                    error TEXT,
                    duration_ms INTEGER
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO hermes_traces "
                "(created_at, source, model, request_body, response_body, error, duration_ms) "
                "VALUES ('2024-01-01T00:00:00', 'chat', 'm', '', '', NULL, 100)"
            )
        )
        conn.commit()
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def _columns(eng, table: str) -> set[str]:
    with eng.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def test_migration_adds_tenant_id_and_attempts(legacy_engine):
    db_module._migrate_hermes_traces()
    cols = _columns(legacy_engine, "hermes_traces")
    assert "tenant_id" in cols
    assert "attempts" in cols


def test_migration_backfills_defaults_for_existing_rows(legacy_engine):
    db_module._migrate_hermes_traces()
    with legacy_engine.connect() as conn:
        row = conn.execute(
            text("SELECT tenant_id, attempts FROM hermes_traces ORDER BY id LIMIT 1")
        ).first()
        assert row is not None
        assert row[0] == "default"
        assert row[1] == 1


def test_migration_is_idempotent(legacy_engine):
    db_module._migrate_hermes_traces()
    cols_before = _columns(legacy_engine, "hermes_traces")
    # Second call should be a no-op (no ALTER, no error).
    db_module._migrate_hermes_traces()
    cols_after = _columns(legacy_engine, "hermes_traces")
    assert cols_before == cols_after
