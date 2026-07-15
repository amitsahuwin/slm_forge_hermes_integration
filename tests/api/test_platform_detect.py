"""Unit tests for host-platform detection (shared backend-default source)."""
from __future__ import annotations

import pytest

from apps.api.services import platform_detect as pd


def test_env_darwin_defaults_to_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "Darwin")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_ARCH", "arm64")
    monkeypatch.delenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", raising=False)
    facts = pd.detect()
    assert facts.os == "darwin"
    assert facts.default_backend == "mlx"
    assert "macOS" in facts.platform_label
    assert pd.default_backend() == "mlx"


def test_env_linux_with_nvidia_defaults_to_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "Linux")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_ARCH", "x86_64")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "true")
    facts = pd.detect()
    assert facts.os == "linux"
    assert facts.has_nvidia_gpu is True
    assert facts.default_backend == "cuda"
    assert "NVIDIA" in facts.platform_label


def test_env_linux_without_nvidia_falls_back_to_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "linux")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "false")
    facts = pd.detect()
    assert facts.default_backend == "cuda"
    assert "NVIDIA" not in facts.platform_label


def test_bare_metal_detection_used_when_env_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No SLM_FORGE_PLATFORM_* → in-process detection path.
    monkeypatch.delenv("SLM_FORGE_PLATFORM_OS", raising=False)
    monkeypatch.delenv("SLM_FORGE_PLATFORM_ARCH", raising=False)
    monkeypatch.delenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", raising=False)
    monkeypatch.setattr(pd.py_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pd.py_platform, "machine", lambda: "arm64")
    facts = pd.detect()
    assert facts.os == "darwin"
    assert facts.default_backend == "mlx"


def test_bare_metal_linux_probes_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_PLATFORM_OS", raising=False)
    monkeypatch.setattr(pd.py_platform, "system", lambda: "Linux")
    monkeypatch.setattr(pd.py_platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(pd, "_has_nvidia_local", lambda: True)
    facts = pd.detect()
    assert facts.os == "linux"
    assert facts.has_nvidia_gpu is True
    assert facts.default_backend == "cuda"
