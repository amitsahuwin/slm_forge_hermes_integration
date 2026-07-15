"""RegisteredModel — a user-added entry in the dynamic model catalog.

The built-in catalog (:data:`apps.api.services.model_catalog.CATALOG_V2`) ships
a fixed set of logical models. This table lets users extend it at runtime by
registering a HuggingFace repo id through the Models tab. Entries are **global**
(visible to every tenant, like the built-in seeds); ``created_by_*`` records
provenance only. Each row maps 1:1 to a ``CatalogModel`` with a single
``BackendVariant`` and is merged into the catalog by ``effective_catalog()``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class RegisteredModel(SQLModel, table=True):
    __tablename__ = "registered_models"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)

    # Logical catalog key (unique), e.g. "qwen2.5-1.5b-instruct".
    key: str = Field(index=True, unique=True)
    label: str
    family: str
    size_params: str
    recommended_method: str = "lora"

    # The single backend variant this entry provides.
    backend: str  # "mlx" | "cuda"
    model_id: str = Field(index=True, unique=True)  # HF repo id
    min_memory_gb: float = 0.0
    quant: str | None = None
    status: str = "untested"  # "stable" | "untested" | "broken"
    gated: bool = False
    notes: str = ""

    # Provenance (not used for visibility — registry is global).
    created_by_user_id: str
    created_by_tenant_id: str
    created_at: datetime = Field(default_factory=_now)