"""Phase O / A6 — Run.trainer_backend field, RunCreate passthrough, migration."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import text


def test_run_model_defaults_to_mlx() -> None:
    from apps.api.models.run import Run

    run = Run(dataset="demo", base_model="any/model")
    assert run.trainer_backend == "mlx"


def test_run_create_schema_defaults_and_passthrough() -> None:
    from apps.api.routers.runs import RunCreate

    assert RunCreate(dataset="demo").trainer_backend == "mlx"
    payload = RunCreate(dataset="demo", trainer_backend="cuda")
    assert payload.trainer_backend == "cuda"
    # create_run() does Run(**payload.model_dump()) — must round-trip.
    from apps.api.models.run import Run

    run = Run(**payload.model_dump())
    assert run.trainer_backend == "cuda"


def test_run_serializes_backend_and_claim_fields() -> None:
    """Phase S — the frontend types rely on these keys in Run responses."""
    from apps.api.models.run import Run

    payload = Run(dataset="demo", base_model="any/model").model_dump()
    for key in ("trainer_backend", "claimed_by", "claimed_at"):
        assert key in payload, f"Run response contract missing '{key}'"
    assert payload["trainer_backend"] == "mlx"
    assert payload["claimed_by"] is None


def test_migration_list_contains_trainer_backend() -> None:
    from apps.api.services import db

    cols = [c for c, _t in db._RUN_MIGRATIONS]
    assert "trainer_backend" in cols


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reload the db module against a throwaway SQLite file."""
    monkeypatch.setenv("SLM_FORGE_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from apps.api.services import db

    db = importlib.reload(db)
    yield db
    db.engine.dispose()
    # Restore the module for other tests (env var reverts via monkeypatch).
    monkeypatch.undo()
    importlib.reload(db)


def test_init_db_fresh_and_idempotent(fresh_db) -> None:
    fresh_db.init_db()
    with fresh_db.engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)"))}
    assert "trainer_backend" in cols
    # Second boot must be a no-op, not an "duplicate column" error.
    fresh_db.init_db()


def test_forward_migration_adds_column_to_legacy_table(fresh_db) -> None:
    """Simulate a pre-Phase-O database: runs table without trainer_backend."""
    with fresh_db.engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE runs ("
            "id INTEGER PRIMARY KEY, dataset TEXT, base_model TEXT, status TEXT)"
        ))
        conn.commit()

    fresh_db._migrate_runs()

    with fresh_db.engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)"))}
    assert "trainer_backend" in cols
