"""Trainer backend registry (Phase O; auto-detect added in Phase T).

Backends are selected via the ``SLM_FORGE_TRAINER_BACKEND`` env var. When
it is unset/empty the backend is auto-detected from the host
(``packages._platform.recommended_backend``): ``mlx`` on Apple Silicon,
``cuda`` on a Linux/NVIDIA box, falling back to ``DEFAULT_BACKEND`` when
inconclusive. Phase Q registers ``cuda`` here; nothing else in the codebase
needs to change to add a backend.
"""
from __future__ import annotations

import os

from packages._platform import recommended_backend
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
    """Resolve the trainer backend; fail fast on junk.

    An explicit, non-empty ``SLM_FORGE_TRAINER_BACKEND`` always wins
    (case/whitespace-insensitive). Otherwise the backend is auto-detected
    from the host via :func:`recommended_backend` (Phase T).
    """
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    return _validate(raw or recommended_backend())


def get_backend(name: str | None = None) -> TrainerBackend:
    """Instantiate the backend for ``name`` (or the env-resolved default)."""
    name = resolve_backend_name() if name is None else _validate(name.strip().lower())
    return _REGISTRY[name]()
