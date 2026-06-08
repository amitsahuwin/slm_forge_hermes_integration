"""Worker heartbeat persistence.

Long-running workers (ratchet, trainer, exporter) POST to ``/api/v1/hermes/heartbeat``
every few seconds. We persist the last-seen timestamp in SQLite so the API
process restarting doesn't make every tile look "down" until the workers
re-register.

One row per ``worker`` name, upserted on each heartbeat.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class WorkerHeartbeat(SQLModel, table=True):
    __tablename__ = "worker_heartbeats"

    worker: str = Field(primary_key=True)
    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: str = ""
