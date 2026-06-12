"""Base model catalogue — views over apps.api.services.model_catalog.

Phase P: the catalog itself lives in ``services/model_catalog.py``
(backend-aware v2). This router exposes two views:

- ``GET /api/v1/models``    — legacy flat shape, frozen for the existing
  React NewRun/NewExperiment pages (``hf_id``/``label``/``notes``...).
  Derived from each model's **mlx** variant.
- ``GET /api/v1/models/v2`` — full backend-aware entries (memory hints,
  per-backend checkpoint ids, status). Phase S's UI consumes this.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from apps.api.services.model_catalog import CATALOG_V2, CatalogModel, default_model_id

router = APIRouter()


class BaseModelInfo(BaseModel):
    hf_id: str
    label: str
    family: str
    size_params: str
    recommended_method: str
    notes: str


def _legacy_view() -> list[BaseModelInfo]:
    default_id = default_model_id("mlx")
    out: list[BaseModelInfo] = []
    for m in CATALOG_V2:
        v = m.backends["mlx"]
        label = m.label
        if v.quant:
            label += f" ({v.quant})"
        if v.status == "broken":
            label += " — ⚠ BROKEN"
        out.append(
            BaseModelInfo(
                hf_id=v.model_id,
                label=label,
                family=m.family,
                size_params=m.size_params,
                recommended_method=m.recommended_method,
                notes=v.notes,
            )
        )
    # Default model first — the UI preselects the first option.
    out.sort(key=lambda b: b.hf_id != default_id)
    return out


@router.get("", response_model=list[BaseModelInfo])
def list_models() -> list[BaseModelInfo]:
    return _legacy_view()


@router.get("/v2", response_model=list[CatalogModel])
def list_models_v2() -> list[CatalogModel]:
    return CATALOG_V2
