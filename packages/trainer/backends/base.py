"""Backend-neutral training contracts (Phase O).

Every training backend (MLX today, CUDA/PEFT in Phase Q) implements
``TrainerBackend``. The orchestration in ``packages.trainer.runner`` only
ever talks to this surface, so adding a backend never touches the
runner, the API, or the metric pipeline.

See ``docs/specs/PHASE_O_SPEC.md`` §4.1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class TrainEvent:
    """One normalized metric observation parsed from trainer stdout.

    ``name`` is one of: ``train_loss``, ``val_loss``, ``learning_rate``,
    ``iters_per_sec``, ``tokens_per_sec``, ``canary_loss`` — the exact
    vocabulary the API's ``/metrics`` endpoint and the UI charts already
    speak. Backends translate their native output into this vocabulary.
    """

    step: int
    name: str
    value: float


class TrainerBackend(ABC):
    """Contract every training backend implements.

    Implementations must be stateless across runs (the worker reuses one
    instance for its whole lifetime) and must not import heavyweight ML
    libraries at module import time — training happens in a subprocess.
    """

    name: ClassVar[str]

    @abstractmethod
    def write_config(self, run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
        """Materialize the backend-native config file and return its path."""

    @abstractmethod
    def build_command(self, config_path: Path) -> list[str] | None:
        """Resolve the training subprocess argv.

        Returns ``None`` when the backend's toolchain is not available on
        this machine (the runner fails the run with
        :meth:`missing_toolchain_message`).
        """

    @abstractmethod
    def parse_line(self, line: str) -> list[TrainEvent]:
        """Map one line of subprocess stdout to zero or more TrainEvents."""

    @abstractmethod
    def missing_toolchain_message(self) -> str:
        """Operator-facing error used when :meth:`build_command` is None."""

    def run_canary_eval(
        self,
        run: dict,
        dataset_dir: Path,
        adapter_dir: Path,
        run_dir: Path,
        env: dict[str, str],
    ) -> float | None:
        """Optional post-training canary evaluation.

        Returns the canary loss, or ``None`` when unsupported / no canary
        split exists / the eval fails. Failures here must never fail the
        run — the runner treats ``None`` as "no canary signal this run".
        """
        return None
