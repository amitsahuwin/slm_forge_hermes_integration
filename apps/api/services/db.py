"""SQLite database initialization."""
from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

# Resolve DB path: Docker mounts /app/data → host ./data
DEFAULT_DB_URL = "sqlite:////app/data/slm_forge.db"
DB_URL = os.environ.get("SLM_FORGE_DB_URL", DEFAULT_DB_URL)

# Ensure parent dir exists when running outside Docker too
if DB_URL.startswith("sqlite:///"):
    db_path = Path(DB_URL.replace("sqlite:///", "", 1))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables. Imports models to register them with SQLModel.metadata."""
    from apps.api.models import metric as _metric  # noqa: F401
    from apps.api.models import run as _run  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
