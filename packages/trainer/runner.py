"""Runs one mlx_lm.lora training job and streams metrics back to the API."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger("trainer.runner")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "datasets"
RUNS_ROOT = PROJECT_ROOT / "runs"

# Regex to parse mlx_lm.lora's stdout
# Example lines:
#   Iter 10: Train loss 2.345, Learning Rate 1.000e-04, It/sec 1.23, Tokens/sec 412.5, ...
#   Iter 20: Val loss 2.123, Val took 4.5s
_ITER_TRAIN = re.compile(
    r"Iter\s+(\d+):\s+Train loss\s+([\d.]+),\s+"
    r"Learning Rate\s+([\d.eE+-]+),\s+"
    r"It/sec\s+([\d.]+),\s+"
    r"Tokens/sec\s+([\d.]+)"
)
_ITER_VAL = re.compile(r"Iter\s+(\d+):\s+Val loss\s+([\d.]+)")


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
    """Generate the YAML config that mlx_lm.lora will consume."""
    cfg: dict[str, Any] = {
        "model": run["base_model"],
        "train": True,
        "data": str(dataset_dir),
        "fine_tune_type": run["method"],
        "num_layers": run["num_layers"],
        "batch_size": run["batch_size"],
        "iters": run["iters"],
        "learning_rate": run["learning_rate"],
        "val_batches": 25,
        "steps_per_report": 10,
        "steps_per_eval": max(20, run["iters"] // 10),
        "save_every": max(50, run["iters"] // 4),
        "adapter_path": str(adapter_dir),
        "max_seq_length": run["max_seq_length"],
        "grad_checkpoint": run["grad_checkpoint"],
        "seed": run["seed"],
    }
    adapter_dir.parent.mkdir(parents=True, exist_ok=True)
    cfg_path = adapter_dir.parent / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return cfg_path


def _ensure_mlx_lm_available() -> bool:
    if shutil.which("mlx_lm.lora") is None:
        log.error(
            "mlx_lm.lora not found on PATH. Install: uv sync --extra trainer "
            "(or `pip install mlx-lm`). Then re-run."
        )
        return False
    return True


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

    if not _ensure_mlx_lm_available():
        _patch_run(
            api_url,
            run_id,
            status="failed",
            error_message="mlx_lm.lora CLI not found. Run `uv sync --extra trainer`.",
        )
        return

    run_dir = RUNS_ROOT / str(run_id)
    adapter_dir = run_dir / "adapter"
    config_path = _write_yaml_config(run, dataset_dir, adapter_dir)

    log.info("Run #%s: config written to %s", run_id, config_path)
    log.info("Run #%s: starting mlx_lm.lora subprocess...", run_id)

    _patch_run(api_url, run_id, status="running")

    cmd = ["mlx_lm.lora", "--config", str(config_path)]
    log.info("Run #%s: $ %s", run_id, " ".join(cmd))

    log_path = run_dir / "training.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    final_train_loss: float | None = None
    final_val_loss: float | None = None

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # line-buffered
            cwd=str(PROJECT_ROOT),
        )

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            log_file.write(line + "\n")
            log_file.flush()

            # Mirror to our stdout too
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
        log.info("Run #%s: completed successfully.", run_id)
        _patch_run(
            api_url,
            run_id,
            status="completed",
            adapter_path=str(adapter_dir),
            final_train_loss=final_train_loss,
            final_val_loss=final_val_loss,
        )
    else:
        msg = f"mlx_lm.lora exited with code {proc.returncode}. See {log_path}"
        log.error("Run #%s: %s", run_id, msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
