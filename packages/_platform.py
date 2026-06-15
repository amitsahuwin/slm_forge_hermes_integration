"""Host platform detection (Phase T).

A dependency-free helper used to pick sane defaults across macOS (Apple
Silicon + MLX) and Linux (NVIDIA + CUDA). It must stay stdlib-only and must
never import heavyweight ML libraries — the trainer's backend registry
imports it at module load, and detection has to work before any extra is
installed. Detection helpers never raise: a probe that fails or finds
nothing returns a safe negative.

See ``docs/specs/PHASE_T_SPEC.md`` §3.1.
"""
from __future__ import annotations

import functools
import os
import platform
import shutil
import subprocess

# Inconclusive-platform fallback. Kept in lockstep with
# ``packages.trainer.backends.DEFAULT_BACKEND`` (asserted by tests) so there
# is a single canonical default.
DEFAULT_BACKEND = "mlx"


@functools.lru_cache(maxsize=1)
def os_name() -> str:
    """Return ``"darwin"``, ``"linux"``, ``"windows"`` (or the raw token)."""
    return platform.system().strip().lower()


@functools.lru_cache(maxsize=1)
def machine() -> str:
    """Lower-cased CPU architecture (``arm64``, ``x86_64``, ``aarch64``…)."""
    return platform.machine().strip().lower()


@functools.lru_cache(maxsize=1)
def is_apple_silicon() -> bool:
    """True on an Apple-Silicon Mac (the MLX/Metal training target)."""
    return os_name() == "darwin" and machine() in {"arm64", "aarch64"}


@functools.lru_cache(maxsize=1)
def has_nvidia_gpu() -> bool:
    """Best-effort detection of a usable NVIDIA GPU. Never raises.

    Two cheap, import-free probes:
      1. ``nvidia-smi`` resolvable on ``PATH`` and exits 0, OR
      2. the NVIDIA kernel driver is present (``/proc/driver/nvidia``) —
         covers minimal containers where the CLI isn't installed.
    """
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            proc = subprocess.run(
                [smi, "-L"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and "GPU" in proc.stdout:
                return True
        except (OSError, subprocess.SubprocessError):
            # Fall through to the procfs probe rather than failing.
            pass

    # Linux kernel-driver fallback (no CLI required).
    return bool(os.path.exists("/proc/driver/nvidia/gpus"))


def recommended_backend() -> str:
    """Pick the trainer backend that matches this host.

    Apple Silicon → ``mlx``; a Linux/NVIDIA host → ``cuda``; anything else
    (inconclusive) → :data:`DEFAULT_BACKEND`. Apple Silicon wins over a
    reported GPU because MLX/Metal is the right path on a Mac.
    """
    if is_apple_silicon():
        return "mlx"
    if has_nvidia_gpu():
        return "cuda"
    return DEFAULT_BACKEND


def summary() -> dict:
    """Compact dict for logging / a ``--version``-style banner."""
    return {
        "os": os_name(),
        "machine": machine(),
        "apple_silicon": is_apple_silicon(),
        "nvidia_gpu": has_nvidia_gpu(),
        "backend": recommended_backend(),
    }
