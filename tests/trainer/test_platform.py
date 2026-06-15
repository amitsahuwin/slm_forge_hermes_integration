"""Phase T / T1 — host platform detection + recommended backend.

These tests are deliberately stdlib-only (no torch/mlx/fastapi imports) so
they run on any interpreter, including a bare system Python, exactly like
the backend registry they feed.
"""
from __future__ import annotations

import pytest

from packages import _platform


def test_os_name_is_known_token() -> None:
    assert _platform.os_name() in {"darwin", "linux", "windows"}


def test_machine_is_lowercased_nonempty() -> None:
    m = _platform.machine()
    assert isinstance(m, str)
    assert m == m.lower()


def test_recommended_backend_prefers_mlx_on_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_platform, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(_platform, "has_nvidia_gpu", lambda: False)
    assert _platform.recommended_backend() == "mlx"


def test_recommended_backend_prefers_cuda_on_linux_nvidia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_platform, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(_platform, "has_nvidia_gpu", lambda: True)
    assert _platform.recommended_backend() == "cuda"


def test_recommended_backend_apple_silicon_wins_over_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Mac that somehow also reports an NVIDIA GPU still trains via MLX.
    monkeypatch.setattr(_platform, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(_platform, "has_nvidia_gpu", lambda: True)
    assert _platform.recommended_backend() == "mlx"


def test_recommended_backend_falls_back_when_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_platform, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(_platform, "has_nvidia_gpu", lambda: False)
    assert _platform.recommended_backend() == _platform.DEFAULT_BACKEND
    assert _platform.recommended_backend() == "mlx"


def test_has_nvidia_gpu_never_raises_without_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the PATH probe to miss and the procfs probe to be absent; the
    # function must degrade to False, never raise.
    _platform.has_nvidia_gpu.cache_clear()
    monkeypatch.setattr(_platform.shutil, "which", lambda _name: None)
    monkeypatch.setattr(_platform.os.path, "exists", lambda _p: False)
    try:
        result = _platform.has_nvidia_gpu()
    finally:
        _platform.has_nvidia_gpu.cache_clear()
    assert result is False


def test_summary_shape() -> None:
    s = _platform.summary()
    assert set(s) == {"os", "machine", "apple_silicon", "nvidia_gpu", "backend"}
    assert s["backend"] in {"mlx", "cuda"}
    assert isinstance(s["apple_silicon"], bool)
    assert isinstance(s["nvidia_gpu"], bool)
