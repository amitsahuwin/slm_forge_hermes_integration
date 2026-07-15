"""ModelDownloadJob model — one background HuggingFace model registration job.

Durable, DB-backed job state (like ``IngestJob``) so a registration survives an
API restart and is tenant-scoped. The composite id surfaced in the Jobs tab is
``modeldownload:<id>``. The job validates an HF repo via the Hub API and, on
success, upserts a global :class:`~apps.api.models.registered_model.RegisteredModel`
so the model appears in the dynamic catalog everywhere.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


class ModelDownloadStatus(str, Enum):  # noqa: UP042 — matches IngestStatus
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


class ModelDownloadJob(SQLModel, table=True):
    __tablename__ = "model_download_jobs"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)

    # Multi-tenancy — captured at row creation from the request's Identity.
    tenant_id: str = Field(index=True)
    user_id: str

    # Requested HF repo id, e.g. "Qwen/Qwen2.5-1.5B-Instruct".
    hf_id: str
    # Backend the entry targets; may be user-supplied override or auto-detected.
    target_backend: str  # "mlx" | "cuda"

    status: ModelDownloadStatus = ModelDownloadStatus.QUEUED

    # Populated as the job runs / on success.
    registered_key: str | None = None
    detected_family: str | None = None
    detected_params: str | None = None
    detected_arch: str | None = None
    gated: bool = False

    error_message: str | None = None

    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None