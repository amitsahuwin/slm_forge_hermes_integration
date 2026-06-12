"""Trainer backend registry (Phase O).

Backends are selected via the ``SLM_FORGE_TRAINER_BACKEND`` env var
(default ``mlx``). Phase Q registers ``cuda`` here; nothing else in the
codebase needs to change to add a backend.
"""
from __future__ import annotations

import os

from packages.trainer.backends.base import TrainerBackend, TrainEvent
from packages.trainer.backends.cuda import CudaBackend
from packages.trainer.backends.mlx import MlxBackend

__all__ = [
    "DEFAULT_BACKEND",
    "ENV_VAR",
    "TrainEvent",
    "TrainerBackend",
    "get_backend",
    "resolve_backend_name",
]

DEFAULT_BACKEND = "mlx"
ENV_VAR = "SLM_FORGE_TRAINER_BACKEND"

_REGISTRY: dict[str, type[TrainerBackend]] = {
    MlxBackend.name: MlxBackend,
    CudaBackend.name: CudaBackend,
}


def _validate(name: str) -> str:
    if name not in _REGISTRY:
        valid = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown trainer backend '{name}' "
            f"(set via ${ENV_VAR}). Valid backends: {valid}"
        )
    return name


def resolve_backend_name() -> str:
    """Read ``SLM_FORGE_TRAINER_BACKEND``; default 'mlx'; fail fast on junk."""
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    return _validate(raw or DEFAULT_BACKEND)


def get_backend(name: str | None = None) -> TrainerBackend:
    """Instantiate the backend for ``name`` (or the env-resolved default)."""
    name = resolve_backend_name() if name is None else _validate(name.strip().lower())
    return _REGISTRY[name]()
