"""Session = an autoresearch run = a sequence of training iterations."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class SessionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetMetric(str, Enum):
    VAL_LOSS = "val_loss"
    CANARY_LOSS = "canary_loss"


def _now() -> datetime:
    return datetime.now(UTC)


class TrainingSession(SQLModel, table=True):
    __tablename__ = "sessions"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    name: str
    dataset: str
    base_model: str
    method: str = "lora"

    # Phase U — which training backend runs every iteration of this session
    # ("mlx" | "cuda" | ...). Plain str (not enum), mirroring Run: the API must
    # accept backends this deployment's workers may not implement yet. Pinned
    # for the whole session; the ratchet loop stamps it onto each child Run.
    trainer_backend: str = "mlx"

    # Baseline hyperparams (iteration 0 uses these)
    iters: int = 100
    batch_size: int = 4
    learning_rate: float = 1e-4
    num_layers: int = 16
    max_seq_length: int = 2048
    # Threaded onto every child Run by the ratchet loop. Default ON — the
    # memory-safe choice on unified-memory hosts (see Run model).
    grad_checkpoint: bool = True

    # Session-level controls
    max_rounds: int = 8
    plateau_patience: int = 3
    min_delta: float = 0.005  # require this much val_loss improvement to "accept"
    target_metric: TargetMetric = TargetMetric.VAL_LOSS
    canary_drift_threshold: float = 0.3  # |canary - val| above this → warning

    status: SessionStatus = SessionStatus.QUEUED
    current_round: int = 0
    best_run_id: int | None = None
    best_metric_value: float | None = None
    error_message: str | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Phase C — multi-tenancy. Same shape as Run.
    tenant_id: str | None = Field(default=None, index=True)
    user_id: str | None = Field(default=None, index=True)
    role: str | None = None
