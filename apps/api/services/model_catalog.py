"""Model catalog v2 — backend-aware (Phase P).

One *logical* model (``CatalogModel``) maps to per-backend *physical*
checkpoints (``BackendVariant``): the MLX 4-bit community conversion for
Apple Silicon, and the full-precision HF repo Phase Q's CUDA backend will
quantize with bitsandbytes. ``min_memory_gb`` values are planning
estimates until the Mac smoke-test matrix (``make smoke-model``) replaces
them with measured peaks and promotes ``status`` to ``"stable"``.

See ``docs/specs/PHASE_P_SPEC.md``.
"""
from __future__ import annotations

import logging
import os

from pydantic import BaseModel

log = logging.getLogger(__name__)

ENFORCE_ENV = "SLM_FORGE_ENFORCE_CATALOG"
_FALSY = {"0", "false", "no", "off"}


_GATED_NOTE = (
    "Gated HF repo — accept the license once on the model page, then provide "
    "HF_TOKEN (set in .env; the worker loads it at startup)."
)


class BackendVariant(BaseModel):
    model_id: str
    min_memory_gb: float
    quant: str | None = None
    status: str = "untested"  # "stable" | "untested" | "broken"
    # Phase U — requires HF license acceptance + HF_TOKEN to download.
    gated: bool = False
    notes: str = ""


class CatalogModel(BaseModel):
    key: str
    label: str
    family: str
    size_params: str
    recommended_method: str = "lora"
    backends: dict[str, BackendVariant]


DEFAULT_MODEL_KEY = "qwen2.5-3b-instruct"

CATALOG_V2: list[CatalogModel] = [
    CatalogModel(
        key="qwen2.5-3b-instruct",
        label="Qwen 2.5 3B Instruct",
        family="qwen",
        size_params="3B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/Qwen2.5-3B-Instruct-4bit",
                min_memory_gb=6, quant="4bit", status="stable",
                notes="Default. Pre-quantized → QLoRA. Proven on mlx-lm 0.31+.",
            ),
            "cuda": BackendVariant(
                model_id="Qwen/Qwen2.5-3B-Instruct",
                min_memory_gb=8, quant="nf4",
                notes="Phase Q: bitsandbytes NF4 QLoRA.",
            ),
        },
    ),
    CatalogModel(
        key="llama-3.2-3b-instruct",
        label="Llama 3.2 3B Instruct",
        family="llama",
        size_params="3B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/Llama-3.2-3B-Instruct-4bit",
                min_memory_gb=6, quant="4bit", status="stable",
                notes="Strong general-purpose baseline.",
            ),
            "cuda": BackendVariant(
                model_id="meta-llama/Llama-3.2-3B-Instruct",
                min_memory_gb=8, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
    CatalogModel(
        key="qwen2.5-7b-instruct",
        label="Qwen 2.5 7B Instruct",
        family="qwen",
        size_params="7B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/Qwen2.5-7B-Instruct-4bit",
                min_memory_gb=9, quant="4bit", status="stable",
                notes="Larger, slower. Comfortable on 36GB M3 Max.",
            ),
            "cuda": BackendVariant(
                model_id="Qwen/Qwen2.5-7B-Instruct",
                min_memory_gb=12, quant="nf4",
            ),
        },
    ),
    CatalogModel(
        key="gemma-3-4b-it",
        label="Gemma 3 4B Instruct",
        family="gemma",
        size_params="4B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/gemma-3-4b-it-4bit",
                min_memory_gb=7, quant="4bit",
                notes="gemma3 arch ships in mlx-lm 0.31.x. Gated HF repo.",
            ),
            "cuda": BackendVariant(
                model_id="google/gemma-3-4b-it",
                min_memory_gb=10, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
    CatalogModel(
        key="gemma-3-12b-it",
        label="Gemma 3 12B Instruct",
        family="gemma",
        size_params="12B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/gemma-3-12b-it-4bit",
                min_memory_gb=16, quant="4bit",
                notes="Keep max_seq_length ≤ 2048 and grad_checkpoint on 36GB.",
            ),
            "cuda": BackendVariant(
                model_id="google/gemma-3-12b-it",
                min_memory_gb=18, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
    CatalogModel(
        key="gemma-4-e2b-it",
        label="Gemma 4 E2B Instruct (MatFormer)",
        family="gemma",
        size_params="~5B raw / 2B effective",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/gemma-4-E2B-it-4bit",
                min_memory_gb=10, quant="4bit",
                notes="gemma4 arch ships in mlx-lm 0.31.x. Text-only fine-tuning.",
            ),
            "cuda": BackendVariant(
                model_id="google/gemma-4-E2B-it",
                min_memory_gb=12, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
    CatalogModel(
        key="gemma-4-e4b-it",
        label="Gemma 4 E4B Instruct (MatFormer)",
        family="gemma",
        size_params="~8B raw / 4B effective",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/gemma-4-E4B-it-4bit",
                min_memory_gb=12, quant="4bit",
                notes="Recommended Gemma sweet spot on 36GB. Text-only fine-tuning.",
            ),
            "cuda": BackendVariant(
                model_id="google/gemma-4-E4B-it",
                min_memory_gb=17, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
    CatalogModel(
        key="mistral-7b-instruct-v0.3",
        label="Mistral 7B Instruct v0.3",
        family="mistral",
        size_params="7B",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/Mistral-7B-Instruct-v0.3-4bit",
                min_memory_gb=9, quant="4bit",
                notes="Loads via llama arch — long-supported in mlx-lm.",
            ),
            "cuda": BackendVariant(
                model_id="mistralai/Mistral-7B-Instruct-v0.3",
                min_memory_gb=12, quant="nf4",
            ),
        },
    ),
    CatalogModel(
        key="gemma-3n-e2b-it",
        label="Gemma 3n E2B Instruct",
        family="gemma",
        size_params="~5B raw / 2B effective",
        backends={
            "mlx": BackendVariant(
                model_id="mlx-community/gemma-3n-E2B-it-bf16",
                min_memory_gb=10, quant=None, status="broken",
                notes="KeyError in sanitize() on mlx-lm 0.31.3 (checkpoint-specific; "
                      "latest release as of 2026-06). Prefer gemma-4-e2b-it.",
            ),
            "cuda": BackendVariant(
                model_id="google/gemma-3n-E2B-it",
                min_memory_gb=12, quant="nf4", gated=True, notes=_GATED_NOTE,
            ),
        },
    ),
]

_BY_KEY: dict[str, CatalogModel] = {m.key: m for m in CATALOG_V2}


def _registered_as_catalog_models() -> list[CatalogModel]:
    """Load user-registered models from the DB as ``CatalogModel`` entries.

    The DB import is lazy so this module (imported by routers at startup) never
    triggers an import cycle with ``services.db``. If the registry is
    unavailable (e.g. queried before the table exists) we log and degrade to the
    built-in seeds rather than breaking catalog listing/validation.
    """
    try:
        from sqlmodel import Session, select

        from apps.api.models.registered_model import RegisteredModel
        from apps.api.services import db

        with Session(db.engine) as s:
            rows = list(s.exec(select(RegisteredModel)))
    except Exception as exc:  # pragma: no cover - registry unavailable
        log.warning("model registry unavailable; using built-in catalog only: %s", exc)
        return []

    return [
        CatalogModel(
            key=r.key,
            label=r.label,
            family=r.family,
            size_params=r.size_params,
            recommended_method=r.recommended_method,
            backends={
                r.backend: BackendVariant(
                    model_id=r.model_id,
                    min_memory_gb=r.min_memory_gb,
                    quant=r.quant,
                    status=r.status,  # type: ignore[arg-type]
                    gated=r.gated,
                    notes=r.notes,
                )
            },
        )
        for r in rows
    ]


def effective_catalog() -> list[CatalogModel]:
    """Built-in seeds merged with the global DB registry.

    Registered entries are merged by ``key``: a new key is appended; a key that
    collides with a seed contributes/overrides only that single backend variant
    so a registration extends an existing logical model without dropping its
    other backends. Seed order is preserved; registered-only models follow.
    """
    merged: dict[str, CatalogModel] = {m.key: m.model_copy(deep=True) for m in CATALOG_V2}
    order: list[str] = [m.key for m in CATALOG_V2]
    for reg in _registered_as_catalog_models():
        existing = merged.get(reg.key)
        if existing is None:
            merged[reg.key] = reg
            order.append(reg.key)
        else:
            existing.backends.update(reg.backends)
    return [merged[k] for k in order]


def get_model_by_key(key: str) -> CatalogModel | None:
    seed = _BY_KEY.get(key)
    if seed is not None:
        return seed
    for m in _registered_as_catalog_models():
        if m.key == key:
            return m
    return None


def find_by_model_id(model_id: str) -> tuple[CatalogModel, str] | None:
    """Match a physical checkpoint id to (logical model, backend name).

    Seeds are checked first (DB-free hot path), then the registry overlay.
    """
    for m in CATALOG_V2:
        for backend_name, variant in m.backends.items():
            if variant.model_id == model_id:
                return m, backend_name
    for m in _registered_as_catalog_models():
        for backend_name, variant in m.backends.items():
            if variant.model_id == model_id:
                return m, backend_name
    return None


def allowed_model_ids() -> set[str]:
    ids = {v.model_id for m in CATALOG_V2 for v in m.backends.values()}
    ids |= {
        v.model_id for m in _registered_as_catalog_models() for v in m.backends.values()
    }
    return ids


def default_model_id(backend: str = "mlx") -> str:
    return _BY_KEY[DEFAULT_MODEL_KEY].backends[backend].model_id


def _enforcement_enabled() -> bool:
    return os.environ.get(ENFORCE_ENV, "true").strip().lower() not in _FALSY


def validate_run_request(base_model: str, trainer_backend: str) -> str | None:
    """Return an error message for an invalid run request, or None if OK.

    Checks (skipped entirely when ``SLM_FORGE_ENFORCE_CATALOG`` is falsy):
      1. ``base_model`` must be a cataloged checkpoint id (any backend).
      2. The checkpoint must belong to the requested ``trainer_backend``.
      3. The variant must not be marked ``broken``.
    """
    if not _enforcement_enabled():
        return None

    hit = find_by_model_id(base_model)
    if hit is None:
        return (
            f"base_model '{base_model}' is not in the model catalog. "
            f"See GET /api/v1/models/v2 for valid models, or set "
            f"{ENFORCE_ENV}=false to disable catalog enforcement."
        )

    model, backend_name = hit
    if backend_name != trainer_backend:
        expected = model.backends.get(trainer_backend)
        hint = (
            f" Use '{expected.model_id}' instead."
            if expected is not None
            else f" '{model.key}' has no {trainer_backend} variant."
        )
        return (
            f"base_model '{base_model}' is the {backend_name} checkpoint of "
            f"'{model.key}', but trainer_backend is '{trainer_backend}'.{hint}"
        )

    variant = model.backends[backend_name]
    if variant.status == "broken":
        return (
            f"base_model '{base_model}' is marked broken in the catalog: "
            f"{variant.notes or 'no details recorded.'}"
        )
    return None
