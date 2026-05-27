"""Run model — represents a single fine-tuning job."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunMethod(str, Enum):
    LORA = "lora"
    DORA = "dora"
    FULL = "full"


def _now() -> datetime:
    return datetime.now(UTC)


class Run(SQLModel, table=True):
    __tablename__ = "runs"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    dataset: str
    base_model: str
    method: RunMethod = RunMethod.LORA
    iters: int = 200
    batch_size: int = 4
    learning_rate: float = 1.0e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    grad_checkpoint: bool = False
    seed: int = 0

    status: RunStatus = RunStatus.QUEUED
    error_message: str | None = None
    adapter_path: str | None = None
    final_train_loss: float | None = None
    final_val_loss: float | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
