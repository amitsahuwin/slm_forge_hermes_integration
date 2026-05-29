"""SQLite database init + lightweight forward-migrations."""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)

DEFAULT_DB_URL = "sqlite:////app/data/slm_forge.db"
DB_URL = os.environ.get("SLM_FORGE_DB_URL", DEFAULT_DB_URL)

if DB_URL.startswith("sqlite:///"):
    db_path = Path(DB_URL.replace("sqlite:///", "", 1))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})

# Idempotent ADD COLUMN migrations for the `runs` table (Phase 2 schema additions)
_RUN_MIGRATIONS: list[tuple[str, str]] = [
    ("session_id", "INTEGER"),
    ("parent_run_id", "INTEGER"),
    ("iteration_number", "INTEGER"),
    ("was_accepted", "INTEGER"),  # SQLite has no BOOL — uses INTEGER 0/1
    ("mutation_reasoning", "TEXT"),
    ("canary_loss", "REAL"),
]


def _migrate_runs() -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(runs)"))}
        for col, sql_type in _RUN_MIGRATIONS:
            if col not in existing:
                log.info("Migrating: ALTER TABLE runs ADD COLUMN %s %s", col, sql_type)
                conn.execute(text(f"ALTER TABLE runs ADD COLUMN {col} {sql_type}"))
                conn.commit()


def init_db() -> None:
    """Create all tables, then run forward-migrations."""
    from apps.api.models import metric as _metric  # noqa: F401
    from apps.api.models import run as _run  # noqa: F401
    from apps.api.models import session as _session  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_runs()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as s:
        yield s
