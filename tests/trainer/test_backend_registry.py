"""Phase O / A4 — backend registry + SLM_FORGE_TRAINER_BACKEND resolution."""
from __future__ import annotations

import pytest

from packages.trainer.backends import (
    DEFAULT_BACKEND,
    ENV_VAR,
    get_backend,
    resolve_backend_name,
)
from packages.trainer.backends.base import TrainerBackend
from packages.trainer.backends.mlx import MlxBackend


def test_default_backend_is_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert DEFAULT_BACKEND == "mlx"
    assert resolve_backend_name() == "mlx"
    backend = get_backend()
    assert isinstance(backend, MlxBackend)
    assert isinstance(backend, TrainerBackend)
    assert backend.name == "mlx"


def test_env_var_selects_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "mlx")
    assert resolve_backend_name() == "mlx"
    assert isinstance(get_backend(), MlxBackend)


def test_env_var_is_case_and_whitespace_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "  MLX ")
    assert resolve_backend_name() == "mlx"


def test_unknown_backend_raises_with_valid_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "tpu")
    with pytest.raises(ValueError) as exc:
        resolve_backend_name()
    # Error must name the valid backends so the operator can self-serve.
    assert "mlx" in str(exc.value)
    assert "tpu" in str(exc.value)


def test_get_backend_explicit_unknown_raises() -> None:
    with pytest.raises(ValueError):
        get_backend("does-not-exist")


def test_empty_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "")
    assert resolve_backend_name() == "mlx"
