"""Metric — a single (step, metric_name, value) datum from a training run."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class Metric(SQLModel, table=True):
    __tablename__ = "metrics"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    step: int = Field(index=True)
    name: str  # e.g. "train_loss", "val_loss", "tokens_per_sec", "learning_rate"
    value: float
    recorded_at: datetime = Field(default_factory=_now)
