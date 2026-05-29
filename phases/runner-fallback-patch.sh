#!/usr/bin/env bash
# Updates packages/trainer/runner.py to invoke mlx-lm as a Python module
# (`python -m mlx_lm lora`) instead of the CLI script. This bypasses the
# script's shebang line entirely, so a stale interpreter path can't break us.
set -euo pipefail

if [ ! -f "packages/trainer/runner.py" ]; then
    echo "✗ Run from project root."
    exit 1
fi

cat > packages/trainer/runner.py <<'EOF'
"""Runs one mlx_lm.lora training job and streams metrics back to the API.

We invoke mlx-lm as `python -m mlx_lm lora ...` rather than the `mlx_lm.lora`
CLI script. This bypasses the script's shebang line, which on macOS frequently
ends up pointing to an interpreter that no longer exists after Homebrew
or pyenv updates.
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


def _build_mlx_lora_cmd(config_path: Path) -> list[str] | None:
    """Decide how to invoke mlx-lm's LoRA trainer.

    Strategy:
      1. Try `python -m mlx_lm lora --config <path>` (modern; bypasses shebang)
      2. Fall back to `python -m mlx_lm.lora --config <path>` (older layout)
      3. Last resort: try the `mlx_lm.lora` CLI script via sysconfig scripts dir.
    """
    py = sys.executable

    # Detect which module form is available
    try:
        out = subprocess.run(
            [py, "-c", "import mlx_lm; import importlib.util as u; "
                       "print('subcmd' if u.find_spec('mlx_lm.tuner') else 'no')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            # Modern mlx-lm uses `python -m mlx_lm lora ...` (subcommand style).
            # Fallback for old versions is `python -m mlx_lm.lora ...`.
            # Test which form works:
            r1 = subprocess.run(
                [py, "-m", "mlx_lm", "lora", "--help"],
                capture_output=True, text=True, timeout=15,
            )
            if r1.returncode == 0:
                return [py, "-m", "mlx_lm", "lora", "--config", str(config_path)]
            r2 = subprocess.run(
                [py, "-m", "mlx_lm.lora", "--help"],
                capture_output=True, text=True, timeout=15,
            )
            if r2.returncode == 0:
                return [py, "-m", "mlx_lm.lora", "--config", str(config_path)]
    except Exception as e:  # noqa: BLE001
        log.warning("Could not probe mlx_lm invocation form: %s", e)

    # Last resort: CLI script
    scripts = sysconfig.get_path("scripts")
    if scripts:
        candidate = Path(scripts) / "mlx_lm.lora"
        if candidate.exists() and os.access(candidate, os.X_OK):
            # Sanity-check the shebang isn't dangling
            try:
                first = candidate.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                if first.startswith("#!"):
                    interp = first[2:].strip().split()[0]
                    if Path(interp).exists():
                        return [str(candidate), "--config", str(config_path)]
                    log.warning(
                        "mlx_lm.lora script has dangling shebang → %s (file not found)", interp
                    )
            except Exception:  # noqa: BLE001
                pass

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
            f"  Python: {sys.executable}\n"
            f"  Verify with: uv run python -m mlx_lm lora --help"
        )
        log.error(msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg[:500])
        return

    log.info("Run #%s: config written to %s", run_id, config_path)
    log.info("Run #%s: invocation: %s", run_id, " ".join(cmd))

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
        msg = f"mlx_lm exited with code {proc.returncode}. See {log_path}"
        log.error("Run #%s: %s", run_id, msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
EOF

# Also update the Makefile guard to use the module form for its sanity check
python3 - <<'PYEOF'
from pathlib import Path
mk = Path("Makefile")
text = mk.read_text()
# Replace the ensure-trainer-installed body
import re
new = re.sub(
    r'ensure-trainer-installed:.*?(?=^[a-zA-Z_-]+:|\Z)',
    """ensure-trainer-installed: ## Internal: verify mlx-lm is callable before running trainer
\t@if ! uv run python -c "import mlx_lm" 2>/dev/null; then \\
\t\techo ""; \\
\t\techo "✗ mlx-lm Python package is not installed in this project's venv."; \\
\t\techo "  Fix: rm -rf .venv && uv sync --all-extras"; \\
\t\texit 1; \\
\tfi
\t@if ! uv run python -m mlx_lm lora --help >/dev/null 2>&1; then \\
\t\tif ! uv run python -m mlx_lm.lora --help >/dev/null 2>&1; then \\
\t\t\techo ""; \\
\t\t\techo "✗ mlx-lm is installed but 'python -m mlx_lm lora' and 'python -m mlx_lm.lora' both fail."; \\
\t\t\techo "  This usually means an mlx-lm version mismatch."; \\
\t\t\techo "  Fix: rm -rf .venv && uv sync --all-extras --refresh"; \\
\t\t\texit 1; \\
\t\tfi; \\
\tfi
\t@echo "✓ mlx-lm callable."

""",
    text,
    count=1,
    flags=re.DOTALL | re.MULTILINE,
)
mk.write_text(new)
print("✓ Makefile guard updated to use python -m form")
PYEOF

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Runner patched                                                    ║
╚══════════════════════════════════════════════════════════════════════╝

What changed:
  • packages/trainer/runner.py
    - Now invokes mlx-lm as 'python -m mlx_lm lora ...' (bypasses shebang)
    - Falls back to 'python -m mlx_lm.lora ...' (older mlx-lm layout)
    - Last resort: direct CLI script (with shebang validity check)
  • Makefile
    - 'make trainer' guard now uses python -m form for the sanity check

Next:

  # Diagnose first so we know what happened (paste output):
  head -1 .venv/bin/mlx_lm.lora
  ls -la \$(head -1 .venv/bin/mlx_lm.lora | sed 's|^#!||') 2>&1 || echo "(broken shebang confirmed)"
  uv run python -m mlx_lm lora --help | head -3

  # Then re-run the trainer:
  make trainer

MSG
