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

# Phase 2 migrations for runs table
_RUN_MIGRATIONS: list[tuple[str, str]] = [
    ("session_id", "INTEGER"),
    ("parent_run_id", "INTEGER"),
    ("iteration_number", "INTEGER"),
    ("was_accepted", "INTEGER"),
    ("mutation_reasoning", "TEXT"),
    ("canary_loss", "REAL"),
    # Phase O — trainer backend abstraction
    ("trainer_backend", "TEXT DEFAULT 'mlx'"),
    # Phase R — atomic claiming + lease
    ("claimed_by", "TEXT"),
    ("claimed_at", "TIMESTAMP"),
    # PR-2 — auto-generated post-mortem markdown on run-failure transition.
    ("post_mortem", "TEXT"),
    ("post_mortem_status", "TEXT DEFAULT 'skipped'"),
    ("post_mortem_input_hash", "TEXT"),
    ("post_mortem_generated_at", "TIMESTAMP"),
]

# Phase U — sessions table forward-migrations (backend pinned per session)
_SESSION_MIGRATIONS: list[tuple[str, str]] = [
    ("trainer_backend", "TEXT DEFAULT 'mlx'"),
]

# PR-1 A1/A4 — hermes_traces additive migration. Both columns are backfilled
# with safe defaults so existing rows continue to roundtrip cleanly.
# Skill-Activity additions append below; all nullable except ``success``
# which defaults to 1 (true) for legacy rows (rows pre-migration never
# carried an explicit error flag — the original ``error`` column tells the
# real story; ``success`` is a fast-filter materialisation going forward).
_HERMES_TRACE_MIGRATIONS: list[tuple[str, str]] = [
    ("attempts", "INTEGER DEFAULT 1"),
    ("tenant_id", "TEXT DEFAULT 'default'"),
    ("skill_name", "TEXT"),
    ("skill_sha256", "TEXT"),
    ("skill_mtime", "TIMESTAMP"),
    ("run_id", "INTEGER"),
    ("session_id", "INTEGER"),
    ("success", "INTEGER DEFAULT 1"),
]

# Phase 4 — exports table is created by SQLModel; no ALTER needed unless schema changes


def _migrate_table(table: str, migrations: list[tuple[str, str]]) -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        for col, sql_type in migrations:
            if col not in existing:
                log.info("Migrating: ALTER TABLE %s ADD COLUMN %s %s", table, col, sql_type)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"))
                conn.commit()


def _migrate_runs() -> None:
    _migrate_table("runs", _RUN_MIGRATIONS)


def _migrate_sessions() -> None:
    _migrate_table("sessions", _SESSION_MIGRATIONS)


def _migrate_hermes_traces() -> None:
    _migrate_table("hermes_traces", _HERMES_TRACE_MIGRATIONS)


# PR-A — auto_fix_attempt is a new table; create_all handles the initial
# schema. The migrations list exists so PR-B (and beyond) can extend the
# row shape additively without hand-editing prod databases.
_AUTOFIX_MIGRATIONS: list[tuple[str, str]] = []


def _migrate_autofix() -> None:
    _migrate_table("auto_fix_attempt", _AUTOFIX_MIGRATIONS)


# Context-aware-chat spec — additive migrations on the chat tables.
# tenant_id default = 'default' so legacy rows keep working in the
# single-tenant case; everything else is nullable so back-compat holds.
_CHAT_CONVERSATION_MIGRATIONS: list[tuple[str, str]] = [
    ("tenant_id", "TEXT DEFAULT 'default'"),
    ("user_id", "TEXT"),
    ("summary_message_id", "INTEGER"),
    ("last_summarized_at", "TIMESTAMP"),
]

_CHAT_MESSAGE_MIGRATIONS: list[tuple[str, str]] = [
    ("tenant_id", "TEXT DEFAULT 'default'"),
    ("token_estimate", "INTEGER DEFAULT 0"),
]


def _migrate_chat_conversations() -> None:
    _migrate_table("chat_conversations", _CHAT_CONVERSATION_MIGRATIONS)


def _migrate_chat_messages() -> None:
    _migrate_table("chat_messages", _CHAT_MESSAGE_MIGRATIONS)


def init_db() -> None:
    from apps.api.models import autofix as _autofix  # noqa: F401
    from apps.api.models import chat as _chat  # noqa: F401
    from apps.api.models import export as _export  # noqa: F401
    from apps.api.models import heartbeat as _heartbeat  # noqa: F401
    from apps.api.models import hermes_trace as _hermes_trace  # noqa: F401
    from apps.api.models import metric as _metric  # noqa: F401
    from apps.api.models import run as _run  # noqa: F401
    from apps.api.models import session as _session  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate_runs()
    _migrate_sessions()
    _migrate_hermes_traces()
    _migrate_autofix()
    _migrate_chat_conversations()
    _migrate_chat_messages()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as s:
        yield s
