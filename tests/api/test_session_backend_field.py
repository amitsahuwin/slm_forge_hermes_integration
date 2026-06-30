"""Phase U — TrainingSession.trainer_backend field, SessionCreate passthrough,
migration, and create_session catalog validation."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

# ── model + schema ──────────────────────────────────────────────

def test_session_model_defaults_to_mlx() -> None:
    from apps.api.models.session import TrainingSession

    s = TrainingSession(name="exp", dataset="demo", base_model="any/model")
    assert s.trainer_backend == "mlx"


def test_session_create_schema_defaults_and_passthrough() -> None:
    from apps.api.models.session import TrainingSession
    from apps.api.routers.sessions import SessionCreate

    assert SessionCreate(name="exp", dataset="demo").trainer_backend == "mlx"
    payload = SessionCreate(
        name="exp",
        dataset="demo",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        trainer_backend="cuda",
    )
    assert payload.trainer_backend == "cuda"
    # create_session() does TrainingSession(**payload.model_dump()) — must round-trip.
    s = TrainingSession(**payload.model_dump())
    assert s.trainer_backend == "cuda"


def test_session_serializes_trainer_backend() -> None:
    from apps.api.models.session import TrainingSession

    payload = TrainingSession(name="exp", dataset="demo", base_model="any").model_dump()
    assert "trainer_backend" in payload
    assert payload["trainer_backend"] == "mlx"


# ── migration ───────────────────────────────────────────────────

def test_migration_list_contains_trainer_backend() -> None:
    from apps.api.services import db

    cols = [c for c, _t in db._SESSION_MIGRATIONS]
    assert "trainer_backend" in cols


@pytest.fixture()
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLM_FORGE_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from apps.api.services import db

    db = importlib.reload(db)
    yield db
    db.engine.dispose()
    monkeypatch.undo()
    importlib.reload(db)


def test_init_db_fresh_and_idempotent(fresh_db) -> None:
    fresh_db.init_db()
    with fresh_db.engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(sessions)"))}
    assert "trainer_backend" in cols
    fresh_db.init_db()  # second boot must be a no-op


def test_forward_migration_adds_column_to_legacy_sessions(fresh_db) -> None:
    """Simulate a pre-Phase-U database: sessions table without trainer_backend."""
    with fresh_db.engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE sessions ("
            "id INTEGER PRIMARY KEY, name TEXT, dataset TEXT, base_model TEXT, status TEXT)"
        ))
        conn.commit()

    fresh_db._migrate_sessions()

    with fresh_db.engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(sessions)"))}
    assert "trainer_backend" in cols


# ── create_session catalog validation ───────────────────────────

@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _FakeRequest():  # backwards compat shim — Phase D needs a real request
    from tests.api._isolation_helpers import synth_admin_request
    return synth_admin_request()


def test_create_session_valid_cuda(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    from apps.api.models.session import TrainingSession
    from apps.api.routers.sessions import SessionCreate, create_session

    payload = SessionCreate(
        name="exp",
        dataset="demo",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        trainer_backend="cuda",
    )
    s = create_session.__wrapped__(payload, _FakeRequest(), db_session)
    assert s.id is not None
    assert s.trainer_backend == "cuda"
    assert db_session.exec(select(TrainingSession)).first() is not None


def test_create_session_backend_model_mismatch_is_422(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    from apps.api.routers.sessions import SessionCreate, create_session

    # mlx checkpoint id but trainer_backend says cuda → mismatch.
    payload = SessionCreate(
        name="exp",
        dataset="demo",
        base_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
        trainer_backend="cuda",
    )
    with pytest.raises(HTTPException) as exc:
        create_session.__wrapped__(payload, _FakeRequest(), db_session)
    assert exc.value.status_code == 422
