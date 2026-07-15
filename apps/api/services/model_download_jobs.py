"""Background runner for HuggingFace model-registration jobs.

A ``ModelDownloadJob`` row is created ``queued`` by the ``POST /models/download``
endpoint. This module drives it to a terminal state:

    queued → processing → (succeeded | failed)

"Download" here means *register + validate* (per the approved design): the job
resolves the HF repo's metadata via the Hub API — confirming it exists, whether
it is gated, its parameter count, architecture and family — and on success
upserts a global :class:`~apps.api.models.registered_model.RegisteredModel` so the
model appears in the dynamic catalog everywhere. The model *weights* continue to
be fetched by the trainer worker at train time (unchanged), so the training path
is untouched.

The runner never raises: every failure is recorded on the row and surfaced in
the Jobs tab.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from apps.api.models.model_download_job import ModelDownloadJob, ModelDownloadStatus
from apps.api.models.registered_model import RegisteredModel
from apps.api.services import db
from apps.api.services import model_catalog as mc

log = logging.getLogger(__name__)

# HF id like "org/name"; segments are [A-Za-z0-9._-]. Guards against injection
# and nonsense input before we ever touch the network.
HF_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_S = 0.5

# Keep strong refs to in-flight tasks so they aren't garbage-collected.
_DOWNLOAD_TASKS: set[asyncio.Task[None]] = set()


class ModelDownloadError(Exception):
    """Recoverable, job-scoped failure (recorded on the row, not propagated)."""


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Pure detection helpers (no I/O — unit-testable in isolation)
# --------------------------------------------------------------------------- #
def infer_backend(hf_id: str) -> str:
    """Infer the trainer backend a repo targets from its id alone.

    MLX needs pre-quantized ``mlx-community`` / ``*-4bit`` repos; everything else
    is treated as a full-precision CUDA (PEFT/bitsandbytes) checkpoint.
    """
    lo = hf_id.lower()
    name = lo.split("/")[-1]
    if lo.startswith("mlx-community/") or "mlx" in name or name.endswith(("-4bit", "-8bit")):
        return "mlx"
    return "cuda"


def _human_params(total: int | None) -> str:
    if not total or total <= 0:
        return "unknown"
    b = total / 1_000_000_000
    if b >= 1:
        return f"{b:.1f}B".replace(".0B", "B")
    m = total / 1_000_000
    return f"{m:.0f}M"


def _infer_family(hf_id: str, arch: str | None) -> str:
    name = hf_id.lower().split("/")[-1]
    for fam in ("llama", "qwen", "gemma", "phi", "mistral", "smollm", "tinyllama"):
        if fam in name or (arch and fam in arch.lower()):
            return fam
    if arch:
        return arch.lower().replace("forcausallm", "").strip("-_") or "other"
    return name.split("-")[0] or "other"


def _slug(hf_id: str) -> str:
    name = hf_id.split("/")[-1].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return slug or "model"


def _unique_key(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _min_memory_gb(params_total: int | None, backend: str) -> float:
    """Coarse memory hint (GB) for the Runs UI. Heuristic, not a guarantee."""
    if not params_total or params_total <= 0:
        return 0.0
    b = params_total / 1_000_000_000
    # 4-bit weights (~0.5 GB/B) + activation/optimizer headroom.
    overhead = 2.0 if backend == "mlx" else 3.0
    return round(b * 0.6 + overhead, 1)


# --------------------------------------------------------------------------- #
# HF Hub metadata fetch (with bounded retry on transient failures)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _ModelMeta:
    gated: bool
    params_total: int | None
    arch: str | None


def _fetch_model_meta(hf_id: str) -> _ModelMeta:
    """Resolve HF metadata; raise ``ModelDownloadError`` on terminal problems.

    Uses ``HF_TOKEN`` from the environment when present (needed to read gated
    repos). Retries only transient HTTP/network errors with backoff + jitter;
    "not found" and "gated/forbidden" are terminal and surfaced immediately.
    """
    import random

    from huggingface_hub import HfApi
    from huggingface_hub.utils import (  # type: ignore[attr-defined]
        GatedRepoError,
        HfHubHTTPError,
        RepositoryNotFoundError,
    )

    token = os.environ.get("HF_TOKEN") or None
    api = HfApi()
    info = None
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            info = api.model_info(hf_id, token=token)
            break
        except RepositoryNotFoundError as e:
            raise ModelDownloadError(
                f"'{hf_id}' was not found on HuggingFace (check the id, and set "
                "HF_TOKEN in .env if it is a private/gated repo)."
            ) from e
        except GatedRepoError as e:
            raise ModelDownloadError(
                f"'{hf_id}' is gated on HuggingFace. Accept its license and set a "
                "valid HF_TOKEN in .env, then retry."
            ) from e
        except (HfHubHTTPError, OSError) as e:  # transient — retry with backoff
            last_exc = e
            if attempt < _MAX_ATTEMPTS:
                time.sleep(
                    _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                )
    if info is None:  # all attempts exhausted
        raise ModelDownloadError(
            f"Could not reach HuggingFace to resolve '{hf_id}': {last_exc}"
        ) from last_exc

    gated = bool(info.gated)
    if gated and not token:
        raise ModelDownloadError(
            f"'{hf_id}' is gated on HuggingFace; set a valid HF_TOKEN in .env "
            "and accept the model license, then retry."
        )
    params_total = info.safetensors.total if info.safetensors else None
    arch = None
    if isinstance(info.config, dict):
        archs = info.config.get("architectures")
        if isinstance(archs, list) and archs:
            arch = str(archs[0])
    return _ModelMeta(gated=gated, params_total=params_total, arch=arch)


# --------------------------------------------------------------------------- #
# Row state transitions (short transactions; never raise on missing row)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _JobPlan:
    hf_id: str
    target_backend: str


def _mark_processing(job_id: int) -> _JobPlan | None:
    with Session(db.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        if job is None:
            log.warning("model-download job %s not found; skipping", job_id)
            return None
        job.status = ModelDownloadStatus.PROCESSING
        job.started_at = _now()
        s.add(job)
        s.commit()
        return _JobPlan(hf_id=job.hf_id, target_backend=job.target_backend)


def _mark_failed(job_id: int, message: str) -> None:
    with Session(db.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        if job is None:  # pragma: no cover - row deleted mid-flight
            return
        job.status = ModelDownloadStatus.FAILED
        job.error_message = message[:1000]
        job.completed_at = _now()
        s.add(job)
        s.commit()


def _mark_succeeded(job_id: int, key: str, meta: _ModelMeta, family: str, params: str) -> None:
    with Session(db.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        if job is None:  # pragma: no cover - row deleted mid-flight
            return
        job.status = ModelDownloadStatus.SUCCEEDED
        job.registered_key = key
        job.detected_family = family
        job.detected_params = params
        job.detected_arch = meta.arch
        job.gated = meta.gated
        job.completed_at = _now()
        s.add(job)
        s.commit()


def _upsert_registered_model(job_id: int, plan: _JobPlan, meta: _ModelMeta) -> tuple[str, str, str]:
    """Insert or update the global registry row for this repo. Returns (key, family, params)."""
    family = _infer_family(plan.hf_id, meta.arch)
    params = _human_params(meta.params_total)
    with Session(db.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        if job is None:  # pragma: no cover - row deleted mid-flight
            raise ModelDownloadError("job row disappeared")
        existing = s.exec(
            select(RegisteredModel).where(RegisteredModel.model_id == plan.hf_id)
        ).first()
        if existing is not None:
            existing.backend = plan.target_backend
            existing.family = family
            existing.size_params = params
            existing.gated = meta.gated
            existing.min_memory_gb = _min_memory_gb(meta.params_total, plan.target_backend)
            s.add(existing)
            s.commit()
            return existing.key, family, params

        taken = {m.key for m in mc.CATALOG_V2} | {
            r.key for r in s.exec(select(RegisteredModel)).all()
        }
        key = _unique_key(_slug(plan.hf_id), taken)
        row = RegisteredModel(
            key=key,
            label=plan.hf_id.split("/")[-1],
            family=family,
            size_params=params,
            recommended_method="lora",
            backend=plan.target_backend,
            model_id=plan.hf_id,
            min_memory_gb=_min_memory_gb(meta.params_total, plan.target_backend),
            quant="nf4" if plan.target_backend == "cuda" else None,
            status="untested",
            gated=meta.gated,
            notes=f"Registered from HuggingFace ({meta.arch or 'unknown arch'}).",
            created_by_user_id=job.user_id,
            created_by_tenant_id=job.tenant_id,
        )
        s.add(row)
        s.commit()
        return key, family, params


async def _run_model_download_job(job_id: int) -> None:
    """Drive a registration job to a terminal state. Never raises."""
    plan = _mark_processing(job_id)
    if plan is None:
        return
    try:
        meta = await asyncio.to_thread(_fetch_model_meta, plan.hf_id)
        key, family, params = _upsert_registered_model(job_id, plan, meta)
        _mark_succeeded(job_id, key, meta, family, params)
        log.info("model-download job %s registered '%s' as key=%s", job_id, plan.hf_id, key)
    except ModelDownloadError as exc:
        _mark_failed(job_id, str(exc))
        log.warning("model-download job %s failed: %s", job_id, exc)
    except Exception as exc:  # defensive — record and move on
        _mark_failed(job_id, f"unexpected error: {exc}")
        log.exception("model-download job %s crashed", job_id)


def _schedule_download(job_id: int) -> None:
    task = asyncio.create_task(_run_model_download_job(job_id))
    _DOWNLOAD_TASKS.add(task)
    task.add_done_callback(_DOWNLOAD_TASKS.discard)