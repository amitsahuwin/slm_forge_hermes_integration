"""HermesTrace schema extension for the Skill-Activity view.

The Traces tab needs richer columns so the UI can answer
"which skill fired during this run?" and "did the skill file change
between calls?" without scraping logs. These tests pin the contract
before the model + migration land.

New columns (all nullable, additive — see ADR in
``docs/adr/`` when this lands):

* ``skill_name``     parsed from ``source`` (``skill:foo`` → ``foo``); indexed.
* ``skill_sha256``   sha256 of the skill markdown at load time (first 16 hex).
* ``skill_mtime``    filesystem mtime of the skill file at load time (UTC).
* ``run_id``         indexed; from the existing ``run_id_ctx`` contextvar.
* ``session_id``     indexed; from the existing ``session_id_ctx`` contextvar.
* ``success``        materialised ``error is None`` so filters are cheap; indexed.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.hermes_trace import HermesTrace
from apps.api.services import db as db_module


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite per test, swapped in for the global engine.

    Mirrors the pattern in ``tests/api/test_hermes_trace_tenant.py`` — only
    the HermesTrace table is materialised so foreign-key checks against
    other models stay out of the way.
    """
    eng = create_engine(f"sqlite:///{tmp_path / 'trace.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


# ---------------------------------------------------------------------------
# Model contract
# ---------------------------------------------------------------------------


def test_model_exposes_skill_columns() -> None:
    """The new attributes must exist on the SQLModel itself, not just in
    the DB. Without these, the API layer cannot return them and the UI
    cannot filter on them."""
    cols = HermesTrace.__table__.columns  # type: ignore[attr-defined]
    for required in (
        "skill_name",
        "skill_sha256",
        "skill_mtime",
        "run_id",
        "session_id",
        "success",
    ):
        assert required in cols, f"HermesTrace is missing column {required!r}"


def test_model_skill_columns_are_nullable_or_have_safe_default() -> None:
    """Backward-compat: any pre-existing row that didn't set these fields
    must still load. Nullable (None) or a safe non-NULL default both work."""
    cols = HermesTrace.__table__.columns  # type: ignore[attr-defined]
    # Everything that came from the model can be NULL except ``success``,
    # which is materialised + indexed and defaults to True.
    for nullable_col in ("skill_name", "skill_sha256", "skill_mtime", "run_id", "session_id"):
        assert cols[nullable_col].nullable, f"{nullable_col} must be nullable"
    success = cols["success"]
    assert success.default is not None or success.server_default is not None, (
        "success column needs a default so the migration is safe for existing rows"
    )


def test_model_indexes_filterable_columns() -> None:
    """Filtering by skill / run / session / success has to be O(log n); without
    indexes the UI's auto-refresh polls would table-scan ``hermes_traces``."""
    cols = HermesTrace.__table__.columns  # type: ignore[attr-defined]
    for indexed in ("skill_name", "run_id", "session_id", "success"):
        assert cols[indexed].index, f"{indexed} must be indexed for filterable lookups"


# ---------------------------------------------------------------------------
# Migration contract
# ---------------------------------------------------------------------------


def test_migration_list_includes_new_columns() -> None:
    """The ``ALTER TABLE`` list backs forward-migration for existing prod DBs.
    The test pins the *names* so a future refactor can't silently drop one."""
    columns = {col for col, _ in db_module._HERMES_TRACE_MIGRATIONS}
    for required in (
        "skill_name",
        "skill_sha256",
        "skill_mtime",
        "run_id",
        "session_id",
        "success",
    ):
        assert required in columns, (
            f"_HERMES_TRACE_MIGRATIONS must add column {required!r} for legacy DBs"
        )


def test_migration_applies_to_legacy_table(tmp_path) -> None:
    """Simulate an older deployment: create the table with only the original
    columns, then run the migration and confirm every new column lands."""
    db_path = tmp_path / "legacy.db"
    eng = create_engine(f"sqlite:///{db_path}")
    # Original schema as of PR-1 A4 (tenant_id was the most recent addition).
    with eng.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE hermes_traces (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP NOT NULL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    model TEXT NOT NULL DEFAULT '',
                    request_body TEXT NOT NULL DEFAULT '',
                    response_body TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    tenant_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO hermes_traces (created_at, source, model) "
                "VALUES (:ts, 'skill:foo', 'qwen3:30b-a3b')"
            ),
            {"ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()

    # Swap the global engine in and run the migration.
    original_engine = db_module.engine
    try:
        db_module.engine = eng
        db_module._migrate_hermes_traces()
        with eng.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(hermes_traces)"))}
        for added in (
            "skill_name",
            "skill_sha256",
            "skill_mtime",
            "run_id",
            "session_id",
            "success",
        ):
            assert added in cols, f"migration did not add {added!r}"
    finally:
        db_module.engine = original_engine


def test_migration_is_idempotent(tmp_path) -> None:
    """Running ``init_db`` repeatedly (e.g. multiple workers, container restart)
    must not raise. ``_migrate_table`` already guards by PRAGMA inspection,
    but pin it here to protect against future refactors."""
    db_path = tmp_path / "idempotent.db"
    eng = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    original_engine = db_module.engine
    try:
        db_module.engine = eng
        db_module._migrate_hermes_traces()
        # Second call must be a no-op, not an error.
        db_module._migrate_hermes_traces()
    finally:
        db_module.engine = original_engine


# ---------------------------------------------------------------------------
# Round-trip — write + read with new columns
# ---------------------------------------------------------------------------


def test_round_trip_with_new_columns(isolated_engine) -> None:
    with Session(isolated_engine) as s:
        s.add(
            HermesTrace(
                source="skill:propose_hyperparam_mutation",
                model="qwen3:30b-a3b",
                request_body="{}",
                response_body='{"ok": 1}',
                duration_ms=42,
                skill_name="propose_hyperparam_mutation",
                skill_sha256="deadbeefcafebabe",
                skill_mtime=datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC),
                run_id=7,
                session_id=3,
                success=True,
            )
        )
        s.commit()
        row = s.exec(select(HermesTrace)).one()
        assert row.skill_name == "propose_hyperparam_mutation"
        assert row.skill_sha256 == "deadbeefcafebabe"
        assert row.run_id == 7
        assert row.session_id == 3
        assert row.success is True


def test_legacy_row_loads_with_null_new_fields(isolated_engine) -> None:
    """Rows written before the migration (only the original columns) must
    still deserialise cleanly. We simulate by inserting with raw SQL and
    confirming the ORM yields ``None`` / safe defaults for the new fields."""
    with isolated_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO hermes_traces "
                "(created_at, source, model, request_body, response_body, "
                " error, duration_ms, attempts, tenant_id) "
                "VALUES (:ts, 'chat', 'qwen3:30b-a3b', '{}', '', NULL, 12, 1, 'default')"
            ),
            {"ts": datetime.now(UTC).isoformat()},
        )
        conn.commit()
    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.skill_name is None
        assert row.skill_sha256 is None
        assert row.skill_mtime is None
        assert row.run_id is None
        assert row.session_id is None
        # success has a default — must not be NULL, must be a bool
        assert row.success in (True, False)
