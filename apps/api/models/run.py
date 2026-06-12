"""Run model — one fine-tuning job (standalone or one iteration of a session)."""
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

    # Phase O — which training backend executes this run ("mlx" | "cuda" | ...).
    # Plain str (not enum): the API must accept runs for backends this
    # deployment's workers may not implement yet.
    trainer_backend: str = "mlx"

    status: RunStatus = RunStatus.QUEUED
    # Phase R — atomic claiming + lease (set by POST /runs/claim).
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    error_message: str | None = None
    adapter_path: str | None = None
    final_train_loss: float | None = None
    final_val_loss: float | None = None

    # Phase 2 — autoresearch fields
    session_id: int | None = Field(default=None, foreign_key="sessions.id", index=True)
    parent_run_id: int | None = None
    iteration_number: int | None = None
    was_accepted: bool | None = None
    mutation_reasoning: str | None = None
    canary_loss: float | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
