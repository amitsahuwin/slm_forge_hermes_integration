"""Export = one LoRA adapter being turned into GGUF artifacts for iPhone."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ExportStatus(str, Enum):
    QUEUED = "queued"
    FUSING = "fusing"
    CONVERTING = "converting"
    QUANTIZING = "quantizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QuantLevel(str, Enum):
    Q4_K_M = "Q4_K_M"
    Q5_K_M = "Q5_K_M"
    Q8_0 = "Q8_0"
    F16 = "F16"


def _now() -> datetime:
    return datetime.now(UTC)


class Export(SQLModel, table=True):
    __tablename__ = "exports"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)

    # Hyperparams snapshotted at export time (so re-running them is reproducible)
    base_model: str
    method: str

    # User-selected quant levels for this export job (comma-separated)
    # default: "Q4_K_M,Q8_0" — Q4 for iPhone, Q8 for reference
    quant_levels: str = "Q4_K_M,Q8_0"

    status: ExportStatus = ExportStatus.QUEUED
    error_message: str | None = None
    progress_text: str | None = None  # human-readable current step

    # Filesystem outputs (filled in as stages complete)
    fused_path: str | None = None
    gguf_f16_path: str | None = None
    gguf_q4_path: str | None = None
    gguf_q5_path: str | None = None
    gguf_q8_path: str | None = None

    # Final sizes (bytes) for the UI to display
    gguf_f16_bytes: int | None = None
    gguf_q4_bytes: int | None = None
    gguf_q5_bytes: int | None = None
    gguf_q8_bytes: int | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
