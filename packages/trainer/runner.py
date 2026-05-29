"""Runs one mlx_lm.lora training job and streams metrics back to the API."""
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
from typing import Any

import httpx
import yaml

log = logging.getLogger("trainer.runner")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "datasets"
RUNS_ROOT = PROJECT_ROOT / "runs"

_ITER_TRAIN = re.compile(
    r"Iter\s+(\d+):\s+Train loss\s+([\d.]+),\s+"
    r"Learning Rate\s+([\d.eE+-]+),\s+"
    r"It/sec\s+([\d.]+),\s+"
    r"Tokens/sec\s+([\d.]+)"
)
_ITER_VAL = re.compile(r"Iter\s+(\d+):\s+Val loss\s+([\d.]+)")


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


def _build_mlx_lora_cmd(config_path: Path) -> list[str] | None:
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


def _patch_run(api_url: str, run_id: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/runs/{run_id}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /runs/%s failed: %s", run_id, e)


def _post_metric(api_url: str, run_id: int, step: int, name: str, value: float) -> None:
    try:
        httpx.post(
            f"{api_url}/api/v1/runs/{run_id}/metrics",
            json={"step": step, "name": name, "value": value},
            timeout=5,
        ).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("POST metric failed: %s", e)


def _write_yaml_config(run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
    batch_size = run["batch_size"]
    val_batches = _safe_val_batches(dataset_dir, batch_size)

    log.info(
        "Run #%s: valid=%d rows, batch_size=%d → val_batches=%d",
        run["id"], _count_jsonl(dataset_dir / "valid.jsonl"), batch_size, val_batches,
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
        "mask_prompt": True,  # loss only on assistant tokens (proper SFT)
    }
    adapter_dir.parent.mkdir(parents=True, exist_ok=True)
    cfg_path = adapter_dir.parent / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return cfg_path


def run_training_job(run: dict, api_url: str) -> None:
    """Run one mlx_lm.lora job and stream metrics back to the API."""
    run_id = run["id"]
    dataset_dir = DATA_ROOT / run["dataset"]

    if not (dataset_dir / "train.jsonl").exists():
        msg = (
            f"Dataset '{run['dataset']}' is missing train.jsonl in {dataset_dir}. "
            "Did you run 'make seed-data'?"
        )
        log.error(msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
        return

    run_dir = RUNS_ROOT / str(run_id)
    adapter_dir = run_dir / "adapter"
    config_path = _write_yaml_config(run, dataset_dir, adapter_dir)

    cmd = _build_mlx_lora_cmd(config_path)
    if cmd is None:
        msg = (
            f"Could not find a working way to invoke mlx_lm.lora.\n"
            f"Python: {sys.executable}\n"
            f"Verify: uv run python -m mlx_lm lora --help"
        )
        log.error(msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg[:500])
        return

    log.info("Run #%s: config → %s", run_id, config_path)
    log.info("Run #%s: cmd → %s", run_id, " ".join(cmd))

    _patch_run(api_url, run_id, status="running")

    log_path = run_dir / "training.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    final_train_loss: float | None = None
    final_val_loss: float | None = None

    env = os.environ.copy()
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"

    with log_path.open("w", encoding="utf-8") as log_file:
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
            log_file.write(line + "\n")
            log_file.flush()
            print(f"  [run #{run_id}] {line}", flush=True)

            m = _ITER_TRAIN.search(line)
            if m:
                step = int(m.group(1))
                train_loss = float(m.group(2))
                lr = float(m.group(3))
                its = float(m.group(4))
                tps = float(m.group(5))
                final_train_loss = train_loss
                _post_metric(api_url, run_id, step, "train_loss", train_loss)
                _post_metric(api_url, run_id, step, "learning_rate", lr)
                _post_metric(api_url, run_id, step, "iters_per_sec", its)
                _post_metric(api_url, run_id, step, "tokens_per_sec", tps)
                continue

            m = _ITER_VAL.search(line)
            if m:
                step = int(m.group(1))
                val_loss = float(m.group(2))
                final_val_loss = val_loss
                _post_metric(api_url, run_id, step, "val_loss", val_loss)

        proc.wait()

    if proc.returncode == 0:
        log.info("Run #%s: completed.", run_id)
        _patch_run(
            api_url, run_id,
            status="completed",
            adapter_path=str(adapter_dir),
            final_train_loss=final_train_loss,
            final_val_loss=final_val_loss,
        )
    else:
        msg = f"mlx_lm exited with code {proc.returncode}. See {log_path}"
        log.error("Run #%s: %s", run_id, msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
