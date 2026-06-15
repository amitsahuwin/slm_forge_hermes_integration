"""Phase O / A4 + Phase T — backend registry + SLM_FORGE_TRAINER_BACKEND
resolution with platform auto-detection.

Contract:
- An explicit, non-empty ``SLM_FORGE_TRAINER_BACKEND`` always wins
  (case/whitespace-insensitive; unknown values raise).
- When the env var is unset/empty, the backend is auto-detected from the
  host via ``packages._platform.recommended_backend`` (Phase T).
"""
from __future__ import annotations

import pytest

from packages.trainer import backends as registry
from packages.trainer.backends import (
    DEFAULT_BACKEND,
    ENV_VAR,
    get_backend,
    resolve_backend_name,
)
from packages.trainer.backends.base import TrainerBackend
from packages.trainer.backends.cuda import CudaBackend
from packages.trainer.backends.mlx import MlxBackend


def test_default_backend_constant_is_mlx() -> None:
    # The inconclusive-platform fallback is still mlx (unchanged contract).
    assert DEFAULT_BACKEND == "mlx"


def test_unset_env_auto_detects_recommended_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(registry, "recommended_backend", lambda: "cuda")
    assert resolve_backend_name() == "cuda"
    backend = get_backend()
    assert isinstance(backend, CudaBackend)
    assert isinstance(backend, TrainerBackend)
    assert backend.name == "cuda"


def test_unset_env_auto_detects_mlx_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(registry, "recommended_backend", lambda: "mlx")
    assert resolve_backend_name() == "mlx"
    assert isinstance(get_backend(), MlxBackend)


def test_empty_env_var_also_auto_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "")
    monkeypatch.setattr(registry, "recommended_backend", lambda: "cuda")
    assert resolve_backend_name() == "cuda"


def test_explicit_env_var_overrides_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even on a "cuda" host, an explicit mlx request must win.
    monkeypatch.setattr(registry, "recommended_backend", lambda: "cuda")
    monkeypatch.setenv(ENV_VAR, "mlx")
    assert resolve_backend_name() == "mlx"
    assert isinstance(get_backend(), MlxBackend)


def test_env_var_is_case_and_whitespace_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "  CUDA ")
    assert resolve_backend_name() == "cuda"


def test_unknown_backend_raises_with_valid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "tpu")
    with pytest.raises(ValueError) as exc:
        resolve_backend_name()
    # Error must name the valid backends so the operator can self-serve.
    assert "mlx" in str(exc.value)
    assert "cuda" in str(exc.value)
    assert "tpu" in str(exc.value)


def test_get_backend_explicit_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_backend("does-not-exist")
