"""Base model catalogue — views over apps.api.services.model_catalog.

Phase P: the catalog itself lives in ``services/model_catalog.py``
(backend-aware v2). This router exposes:

- ``GET /api/v1/models``    — legacy flat shape, frozen for the existing
  React NewRun/NewExperiment pages (``hf_id``/``label``/``notes``...).
  Derived from each model's **mlx** variant.
- ``GET /api/v1/models/v2`` — full backend-aware entries (memory hints,
  per-backend checkpoint ids, status). The UI consumes this.

Dynamic registry (Models tab):

- ``POST   /api/v1/models/download``       — queue a HF register+validate job.
- ``GET    /api/v1/models/registry``       — user-registered rows (for manage).
- ``DELETE /api/v1/models/registry/{key}`` — remove a registered model.

All catalog views read through ``effective_catalog()`` so registered models
appear everywhere (New Run / New Experiment dropdowns) with no hardcoding.
Weights are still fetched by the trainer worker at train time — this path
only registers + validates metadata, so the training path is untouched.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.middleware.auth import requires
from apps.api.models.model_download_job import ModelDownloadJob, ModelDownloadStatus
from apps.api.models.registered_model import RegisteredModel
from apps.api.services import db
from apps.api.services.identity import current_identity
from apps.api.services.model_catalog import (
    CatalogModel,
    default_model_id,
    effective_catalog,
)
from apps.api.services.model_download_jobs import (
    HF_ID_RE,
    _schedule_download,
    infer_backend,
)

log = logging.getLogger(__name__)

router = APIRouter()

_VALID_BACKENDS = {"mlx", "cuda"}


class BaseModelInfo(BaseModel):
    hf_id: str
    label: str
    family: str
    size_params: str
    recommended_method: str
    notes: str


class DownloadRequest(BaseModel):
    hf_id: str
    backend: str | None = None  # optional override; auto-detected when omitted


class DownloadResponse(BaseModel):
    job_id: str
    hf_id: str
    target_backend: str
    status: str


class RegistryEntry(BaseModel):
    key: str
    label: str
    family: str
    size_params: str
    backend: str
    model_id: str
    status: str
    gated: bool
    notes: str


def _legacy_view() -> list[BaseModelInfo]:
    default_id = default_model_id("mlx")
    out: list[BaseModelInfo] = []
    for m in effective_catalog():
        v = m.backends.get("mlx")
        if v is None:  # cuda-only registered models have no mlx variant
            continue
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
    return effective_catalog()


@router.get("/registry", response_model=list[RegistryEntry])
def list_registry() -> list[RegistryEntry]:
    """User-registered models only (the built-in seeds are omitted).

    Global registry — visible to every authenticated tenant, like the seeds.
    Used by the Models tab to render the manage/delete list.
    """
    with Session(db.engine) as s:
        rows = list(s.exec(select(RegisteredModel)))
    return [
        RegistryEntry(
            key=r.key,
            label=r.label,
            family=r.family,
            size_params=r.size_params,
            backend=r.backend,
            model_id=r.model_id,
            status=r.status,
            gated=r.gated,
            notes=r.notes,
        )
        for r in rows
    ]


@router.post("/download", status_code=202, response_model=DownloadResponse)
@requires("create", "model")
async def download_model(request: Request, body: DownloadRequest) -> DownloadResponse:
    """Queue a HuggingFace register+validate job. Returns 202 with a
    ``modeldownload:<id>`` job id the Jobs tab can poll.

    The job validates the repo via the HF Hub API and, on success, upserts a
    global catalog entry. No weights are fetched here.
    """
    hf_id = body.hf_id.strip()
    if not HF_ID_RE.match(hf_id):
        raise HTTPException(
            422,
            "hf_id must look like 'org/name' (letters, digits, '.', '_', '-'), "
            "e.g. 'Qwen/Qwen2.5-1.5B-Instruct'.",
        )

    backend = (body.backend or infer_backend(hf_id)).strip().lower()
    if backend not in _VALID_BACKENDS:
        raise HTTPException(
            422, f"backend must be one of {sorted(_VALID_BACKENDS)}; got '{backend}'."
        )

    identity = current_identity(request)
    with Session(db.engine) as s:
        job = ModelDownloadJob(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            hf_id=hf_id,
            target_backend=backend,
            status=ModelDownloadStatus.QUEUED,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    assert job_id is not None

    log.info(
        "model-download queued job=%s hf_id=%s backend=%s tenant=%s user=%s",
        job_id, hf_id, backend, identity.tenant_id, identity.user_id,
    )
    _schedule_download(job_id)
    return DownloadResponse(
        job_id=f"modeldownload:{job_id}",
        hf_id=hf_id,
        target_backend=backend,
        status=ModelDownloadStatus.QUEUED.value,
    )


@router.delete("/registry/{key}", status_code=204)
@requires("delete", "model")
async def delete_registered(request: Request, key: str) -> None:
    """Remove a user-registered model from the global registry.

    Built-in seeds are not stored in the registry and cannot be deleted here.
    """
    identity = current_identity(request)
    with Session(db.engine) as s:
        row = s.exec(
            select(RegisteredModel).where(RegisteredModel.key == key)
        ).first()
        if row is None:
            raise HTTPException(404, f"registered model '{key}' not found")
        s.delete(row)
        s.commit()
    log.info(
        "model-registry delete key=%s tenant=%s user=%s",
        key, identity.tenant_id, identity.user_id,
    )