"""CUDA backend — PEFT + TRL + bitsandbytes on NVIDIA GPUs (Phase Q).

The worker shells out to ``packages.trainer.cuda_train`` (a separate
process, mirroring the MLX subprocess pattern), which emits one JSON
object per metric on stdout:

    {"event": "metric", "step": 10, "name": "train_loss", "value": 2.5}

``parse_line`` maps those to normalized :class:`TrainEvent`s — structured
JSONL instead of the regex scraping the MLX backend inherited.

Heavy ML libraries are never imported here; the toolchain is probed in a
subprocess so this module stays importable on any machine (including the
Mac, where the cuda backend simply reports its toolchain as missing).

See ``docs/specs/PHASE_Q_SPEC.md``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

from packages.trainer.backends.base import TrainerBackend, TrainEvent
from packages.trainer.backends.dataset_utils import detect_dataset_format

log = logging.getLogger("trainer.backend.cuda")

QUANT_ENV = "SLM_FORGE_CUDA_QUANT"
_REQUIRED_IMPORTS = "import torch, transformers, peft, trl"


class CudaBackend(TrainerBackend):
    """Trains via transformers + PEFT + TRL (+ bitsandbytes NF4 QLoRA)."""

    name: ClassVar[str] = "cuda"

    def write_config(self, run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
        ds_format = detect_dataset_format(dataset_dir)
        mask_prompt = ds_format == "chat"
        quant = os.environ.get(QUANT_ENV, "nf4").strip().lower() or "nf4"

        cfg: dict[str, Any] = {
            "model": run["base_model"],
            "data": str(dataset_dir),
            "dataset_format": ds_format,
            "mask_prompt": mask_prompt,
            "fine_tune_type": run["method"],
            "lora_rank": 16,
            "lora_alpha": 32,
            "batch_size": run["batch_size"],
            "iters": run["iters"],
            "learning_rate": run["learning_rate"],
            "max_seq_length": run["max_seq_length"],
            "grad_checkpoint": run["grad_checkpoint"],
            "seed": run["seed"],
            "quant": quant,
            "adapter_path": str(adapter_dir),
            "steps_per_report": 10,
            "steps_per_eval": max(20, run["iters"] // 10),
        }
        log.info(
            "Run #%s: cuda config — model=%s format=%s mask_prompt=%s quant=%s",
            run["id"], run["base_model"], ds_format, mask_prompt, quant,
        )
        adapter_dir.parent.mkdir(parents=True, exist_ok=True)
        cfg_path = adapter_dir.parent / "config.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg_path

    def build_command(self, config_path: Path) -> list[str] | None:
        probe = subprocess.run(
            [sys.executable, "-c", _REQUIRED_IMPORTS],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode != 0:
            log.debug("cuda toolchain probe failed: %s", probe.stderr.strip()[:200])
            return None
        return [
            sys.executable, "-m", "packages.trainer.cuda_train",
            "--config", str(config_path),
        ]

    def missing_toolchain_message(self) -> str:
        return (
            "CUDA training toolchain not available "
            f"(probe: python -c '{_REQUIRED_IMPORTS}').\n"
            "Install it with: pip install -e '.[trainer-cuda]' "
            "(or uv sync --extra trainer-cuda) on a CUDA machine.\n"
            "Gated HF models also need HF_TOKEN set or `huggingface-cli login`."
        )

    def parse_line(self, line: str) -> list[TrainEvent]:
        s = line.strip()
        if not s.startswith("{"):
            return []
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return []
        if not isinstance(obj, dict) or obj.get("event") != "metric":
            return []
        try:
            return [
                TrainEvent(
                    step=int(obj["step"]),
                    name=str(obj["name"]),
                    value=float(obj["value"]),
                )
            ]
        except (KeyError, TypeError, ValueError):
            return []
