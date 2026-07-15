"""Host-platform detection — single source of truth for backend defaults.

Both the ``/api/v1/platform`` endpoint (smart UI defaults) and the model
registry's backend auto-detect need the same answer: *which trainer backend
does this host default to?* macOS → ``mlx`` (Apple Silicon), Linux + NVIDIA →
``cuda``.

When the API runs in Docker it cannot see the host directly, so
``docker-compose`` forwards the host's detection via ``SLM_FORGE_PLATFORM_*``
env vars (set from ``uname`` in the Makefile). We read those first and only fall
back to in-process detection for the bare-metal case.
"""
from __future__ import annotations

import os
import platform as py_platform
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformFacts:
    os: str  # "darwin" | "linux"
    arch: str  # "arm64" | "x86_64"
    has_nvidia_gpu: bool
    default_backend: str  # "mlx" | "cuda"
    platform_label: str


def _has_nvidia_local() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, timeout=2, check=False
        )
        return result.returncode == 0 and b"GPU" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def detect() -> PlatformFacts:
    """Detect the host platform + its default trainer backend.

    Env vars (Docker mode) take precedence over in-process detection so a
    containerised API on a Mac still reports ``darwin``/``mlx``.
    """
    env_os = os.getenv("SLM_FORGE_PLATFORM_OS")
    if env_os:
        os_name = env_os.lower()
        arch = os.getenv("SLM_FORGE_PLATFORM_ARCH", py_platform.machine()).lower()
        has_nvidia = os.getenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "").lower() == "true"
    else:
        os_name = py_platform.system().lower()
        arch = py_platform.machine().lower()
        has_nvidia = _has_nvidia_local() if os_name == "linux" else False

    if os_name == "darwin":
        default_backend = "mlx"
        platform_label = f"macOS ({arch})"
    elif os_name == "linux" and has_nvidia:
        default_backend = "cuda"
        platform_label = f"Linux ({arch}) + NVIDIA GPU"
    else:
        default_backend = "cuda"
        platform_label = f"Linux ({arch})"

    return PlatformFacts(
        os=os_name,
        arch=arch,
        has_nvidia_gpu=has_nvidia,
        default_backend=default_backend,
        platform_label=platform_label,
    )


def default_backend() -> str:
    """The trainer backend this host defaults to (``mlx`` | ``cuda``)."""
    return detect().default_backend