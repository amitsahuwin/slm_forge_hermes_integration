"""MLX backend — all mlx-lm specific logic, moved verbatim from runner.py.

Phase O extraction: behavior must be bit-identical to the pre-refactor
``packages/trainer/runner.py``. Config YAML keys, command discovery order,
metric regexes, log lines, and the canary-eval algorithm are unchanged.

See ``docs/specs/PHASE_O_SPEC.md`` §4.3.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any, ClassVar

import yaml

from packages.trainer.backends.base import TrainerBackend, TrainEvent

log = logging.getLogger("trainer.backend.mlx")

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_ITER_TRAIN = re.compile(
    r"Iter\s+(\d+):\s+Train loss\s+([\d.]+),\s+"
    r"Learning Rate\s+([\d.eE+-]+),\s+"
    r"It/sec\s+([\d.]+),\s+"
    r"Tokens/sec\s+([\d.]+)"
)
_ITER_VAL = re.compile(r"Iter\s+(\d+):\s+Val loss\s+([\d.]+)")
# Phase E: parse mlx-lm's --test mode output to extract canary loss.
_TEST_LOSS = re.compile(r"Test loss\s+([\d.]+)")


def _count_jsonl(path: Path) -> int:
    """Count non-empty lines in a JSONL file."""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _safe_val_batches(dataset_dir: Path, batch_size: int) -> int:
    """Return a val_batches value that can never trigger the 'not enough examples' error.

    mlx-lm requires: valid examples >= val_batches * batch_size
    So: val_batches <= floor(valid_examples / batch_size)
    We also cap at 25 (the default) so large datasets don't eval forever.
    """
    n_valid = _count_jsonl(dataset_dir / "valid.jsonl")
    if n_valid == 0:
        return 0
    safe = max(1, n_valid // batch_size)
    return min(safe, 25)


def _detect_dataset_format(dataset_dir: Path) -> str:
    """Inspect train.jsonl's first non-empty row and return ``"chat"`` or ``"text"``.

    MLX-LM supports both:
      • chat:  ``{"messages": [{"role": ..., "content": ...}, ...]}``
      • prompt+completion: ``{"prompt": "...", "completion": "..."}``  (also chat-like)
      • text:  ``{"text": "..."}``

    ``mask_prompt: True`` only works for chat/completion formats. If the dataset
    is plain text (the format of all 6 seed datasets) we must set it to False
    or MLX-LM raises ``ValueError("Prompt masking not supported for text dataset.")``.
    """
    train_path = dataset_dir / "train.jsonl"
    if not train_path.exists():
        return "text"  # caller will fail later with a clearer error
    try:
        with train_path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                obj = json.loads(s)
                if not isinstance(obj, dict):
                    return "text"
                if "messages" in obj or ("prompt" in obj and "completion" in obj):
                    return "chat"
                return "text"
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not detect dataset format (%s) — defaulting to text", e)
    return "text"


class MlxBackend(TrainerBackend):
    """Trains via ``mlx_lm lora`` on Apple Silicon (Metal)."""

    name: ClassVar[str] = "mlx"

    # -- config ------------------------------------------------------------

    def write_config(self, run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
        batch_size = run["batch_size"]
        val_batches = _safe_val_batches(dataset_dir, batch_size)
        ds_format = _detect_dataset_format(dataset_dir)
        # ``mask_prompt`` only applies to chat / prompt-completion rows. MLX-LM
        # raises a hard error if we set it on a plain-text dataset.
        mask_prompt = ds_format == "chat"

        log.info(
            "Run #%s: valid=%d rows, batch_size=%d → val_batches=%d, format=%s, mask_prompt=%s",
            run["id"],
            _count_jsonl(dataset_dir / "valid.jsonl"),
            batch_size,
            val_batches,
            ds_format,
            mask_prompt,
        )

        cfg: dict[str, Any] = {
            "model": run["base_model"],
            "train": True,
            "data": str(dataset_dir),
            "fine_tune_type": run["method"],
            "num_layers": run["num_layers"],
            "batch_size": batch_size,
            "iters": run["iters"],
            "learning_rate": run["learning_rate"],
            "val_batches": val_batches,
            "steps_per_report": 10,
            "steps_per_eval": max(20, run["iters"] // 10),
            "save_every": max(50, run["iters"] // 4),
            "adapter_path": str(adapter_dir),
            "max_seq_length": run["max_seq_length"],
            "grad_checkpoint": run["grad_checkpoint"],
            "seed": run["seed"],
            # loss only on assistant tokens for chat-style datasets; for text-style
            # we have to train on all tokens because MLX-LM doesn't support masking
            # there (see ValueError in tuner/datasets.py).
            "mask_prompt": mask_prompt,
        }
        adapter_dir.parent.mkdir(parents=True, exist_ok=True)
        cfg_path = adapter_dir.parent / "config.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        return cfg_path

    # -- command discovery ---------------------------------------------------

    def build_command(self, config_path: Path) -> list[str] | None:
        """Find a working invocation of mlx-lm's LoRA trainer."""
        py = sys.executable

        # Try modern subcommand form: python -m mlx_lm lora
        r1 = subprocess.run(
            [py, "-m", "mlx_lm", "lora", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        if r1.returncode == 0:
            return [py, "-m", "mlx_lm", "lora", "--config", str(config_path)]

        # Try older direct-module form: python -m mlx_lm.lora
        r2 = subprocess.run(
            [py, "-m", "mlx_lm.lora", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        if r2.returncode == 0:
            return [py, "-m", "mlx_lm.lora", "--config", str(config_path)]

        # Last resort: CLI script via sysconfig
        scripts = sysconfig.get_path("scripts")
        if scripts:
            candidate = Path(scripts) / "mlx_lm.lora"
            if candidate.exists() and os.access(candidate, os.X_OK):
                return [str(candidate), "--config", str(config_path)]

        found = shutil.which("mlx_lm.lora")
        if found:
            return [found, "--config", str(config_path)]

        return None

    def missing_toolchain_message(self) -> str:
        return (
            f"Could not find a working way to invoke mlx_lm.lora.\n"
            f"Python: {sys.executable}\n"
            f"Verify: uv run python -m mlx_lm lora --help"
        )

    # -- metric parsing --------------------------------------------------------

    def parse_line(self, line: str) -> list[TrainEvent]:
        m = _ITER_TRAIN.search(line)
        if m:
            step = int(m.group(1))
            return [
                TrainEvent(step=step, name="train_loss", value=float(m.group(2))),
                TrainEvent(step=step, name="learning_rate", value=float(m.group(3))),
                TrainEvent(step=step, name="iters_per_sec", value=float(m.group(4))),
                TrainEvent(step=step, name="tokens_per_sec", value=float(m.group(5))),
            ]
        m = _ITER_VAL.search(line)
        if m:
            return [
                TrainEvent(step=int(m.group(1)), name="val_loss", value=float(m.group(2)))
            ]
        return []

    # -- canary eval -------------------------------------------------------------

    def run_canary_eval(
        self,
        run: dict,
        dataset_dir: Path,
        adapter_dir: Path,
        run_dir: Path,
        env: dict[str, str],
    ) -> float | None:
        """Run mlx-lm in --test mode against canary.jsonl and return the loss.

        Why: the autoresearch ratchet's Goodhart guardrail compares canary loss
        against val loss. This evaluates the trained adapter on the held-out
        canary set and returns a single ``canary_loss`` value.

        Returns ``None`` if the dataset has no canary.jsonl or the eval
        subprocess fails — caller treats that as "no canary signal this run"
        rather than a fatal failure.
        """
        import tempfile

        canary_src = dataset_dir / "canary.jsonl"
        if not canary_src.exists():
            log.info(
                "Run #%s: no canary.jsonl in %s — skipping canary eval",
                run["id"], dataset_dir,
            )
            return None

        n_canary = _count_jsonl(canary_src)
        if n_canary == 0:
            log.info("Run #%s: canary.jsonl is empty — skipping", run["id"])
            return None

        # mlx-lm's --test mode reads test.jsonl from --data. Build a tiny temp
        # dataset dir that just contains the canary set under that name.
        tmp_root = Path(tempfile.mkdtemp(prefix=f"slm_canary_run{run['id']}_"))
        try:
            # mlx-lm requires train.jsonl and valid.jsonl even in --test mode for
            # config validation in some versions. Symlink to the originals to avoid
            # copying large files; fall back to copy if symlink isn't supported.
            for split in ("train.jsonl", "valid.jsonl"):
                src = dataset_dir / split
                if src.exists():
                    try:
                        (tmp_root / split).symlink_to(src.resolve())
                    except OSError:
                        shutil.copy2(src, tmp_root / split)
            shutil.copy2(canary_src, tmp_root / "test.jsonl")

            # test_batches must be small enough that test_batches * batch_size <= n_canary.
            batch_size = max(1, run["batch_size"])
            test_batches = max(1, min(10, n_canary // batch_size))

            # Write a minimal config — reuse base model + adapter, skip training.
            canary_cfg: dict[str, Any] = {
                "model": run["base_model"],
                "train": False,
                "test": True,
                "data": str(tmp_root),
                "fine_tune_type": run["method"],
                "batch_size": batch_size,
                "test_batches": test_batches,
                "adapter_path": str(adapter_dir),
                "max_seq_length": run["max_seq_length"],
            }
            canary_cfg_path = run_dir / "canary_config.yaml"
            with canary_cfg_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(canary_cfg, f, sort_keys=False)

            cmd = self.build_command(canary_cfg_path)
            if cmd is None:
                log.warning("Run #%s: canary cmd unavailable", run["id"])
                return None

            canary_log = run_dir / "canary.log"
            log.info(
                "Run #%s: evaluating canary (%d rows, %d batches) → %s",
                run["id"],
                n_canary,
                test_batches,
                canary_log,
            )

            canary_loss: float | None = None
            with canary_log.open("w", encoding="utf-8") as lf:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                )
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip()
                    lf.write(line + "\n")
                    lf.flush()
                    print(f"  [run #{run['id']} canary] {line}", flush=True)
                    m = _TEST_LOSS.search(line)
                    if m:
                        canary_loss = float(m.group(1))
                proc.wait()

            if proc.returncode != 0:
                log.warning(
                    "Run #%s: canary eval exited %d (no signal recorded)",
                    run["id"],
                    proc.returncode,
                )
                return None
            if canary_loss is None:
                log.warning(
                    "Run #%s: canary eval completed but no 'Test loss' line found",
                    run["id"],
                )
                return None

            log.info("Run #%s: canary_loss=%.4f", run["id"], canary_loss)
            return canary_loss
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
