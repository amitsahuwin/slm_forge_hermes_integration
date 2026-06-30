"""Backend-agnostic training orchestration.

Runs one training job through a :class:`TrainerBackend` (MLX by default)
and streams normalized metrics back to the API. All engine-specific logic
(config format, CLI discovery, stdout parsing, canary eval) lives in
``packages/trainer/backends/``; this module only orchestrates:

    dataset guard → backend.write_config → backend.build_command →
    subprocess stream → backend.parse_line → metric POSTs → final Run patch

Phase O refactor — behavior is identical to the pre-refactor MLX-only
runner. See ``docs/specs/PHASE_O_SPEC.md``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sysconfig
from pathlib import Path
from typing import Any

import httpx

from packages.trainer import transfer
from packages.trainer.backends import TrainerBackend, get_backend

log = logging.getLogger("trainer.runner")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "datasets"
RUNS_ROOT = PROJECT_ROOT / "runs"


def _resolve_dataset_dir(run: dict[str, Any], base: Path) -> Path:
    """Phase D.3 — workers look up the dataset under the Phase-D layout:

      1. ``base/global/<name>/``                        — bundled samples
      2. ``base/users/{tenant_id}/{user_id}/<name>/``    — user uploads

    Falls back to the legacy flat ``base/<name>/`` so a pre-Phase-D run
    that's still in the queue can finish. Returns the first matching
    directory (so a real ``train.jsonl`` check still acts as the final
    gate). If nothing exists, returns the *expected* per-user path so
    the error message points at the right spot.
    """
    name = str(run["dataset"])
    candidates: list[Path] = [base / "global" / name]
    tenant = run.get("tenant_id")
    user = run.get("user_id")
    if tenant and user:
        candidates.append(base / "users" / tenant / user / name)
    candidates.append(base / name)  # legacy flat layout — backwards compat
    for c in candidates:
        if (c / "train.jsonl").exists():
            return c
    # Nothing found — return the expected user path so the failure
    # message guides the operator to the right spot.
    if tenant and user:
        return base / "users" / tenant / user / name
    return base / name


def _patch_run(api_url: str, run_id: int, **fields: Any) -> None:
    try:
        httpx.patch(
            f"{api_url}/api/v1/runs/{run_id}", json=fields, timeout=10
        ).raise_for_status()
    except Exception as e:
        log.warning("PATCH /runs/%s failed: %s", run_id, e)


def _post_metric(api_url: str, run_id: int, step: int, name: str, value: float) -> None:
    try:
        httpx.post(
            f"{api_url}/api/v1/runs/{run_id}/metrics",
            json={"step": step, "name": name, "value": value},
            timeout=5,
        ).raise_for_status()
    except Exception as e:
        log.warning("POST metric failed: %s", e)


def run_training_job(
    run: dict,
    api_url: str,
    backend: TrainerBackend | None = None,
) -> None:
    """Run one training job through ``backend`` and stream metrics to the API.

    ``backend`` defaults to the env-selected backend
    (``SLM_FORGE_TRAINER_BACKEND``, default mlx) so existing callers keep
    working unchanged.
    """
    backend = backend if backend is not None else get_backend()
    run_id = run["id"]
    dataset_dir = _resolve_dataset_dir(run, DATA_ROOT)

    if not (dataset_dir / "train.jsonl").exists():
        # Phase R — remote workers fetch the dataset from the API instead of
        # assuming a shared filesystem.
        if transfer.remote_mode():
            try:
                dataset_dir = transfer.ensure_dataset_local(run["dataset"], api_url)
            except transfer.TransferError as e:
                msg = f"Dataset '{run['dataset']}' could not be fetched: {e}"
                log.error(msg)
                _patch_run(api_url, run_id, status="failed", error_message=msg[:500])
                return
        else:
            msg = (
                f"Dataset '{run['dataset']}' is missing train.jsonl in {dataset_dir}. "
                "Did you run 'make seed-data'?"
            )
            log.error(msg)
            _patch_run(api_url, run_id, status="failed", error_message=msg)
            return

    run_dir = RUNS_ROOT / str(run_id)
    adapter_dir = run_dir / "adapter"
    config_path = backend.write_config(run, dataset_dir, adapter_dir)

    cmd = backend.build_command(config_path)
    if cmd is None:
        msg = backend.missing_toolchain_message()
        log.error(msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg[:500])
        return

    log.info("Run #%s: backend → %s", run_id, backend.name)
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

            for ev in backend.parse_line(line):
                if ev.name == "train_loss":
                    final_train_loss = ev.value
                elif ev.name == "val_loss":
                    final_val_loss = ev.value
                _post_metric(api_url, run_id, ev.step, ev.name, ev.value)

        proc.wait()

    if proc.returncode == 0:
        log.info("Run #%s: completed.", run_id)

        # Canary eval (Phase E). Best-effort: a failure here doesn't fail
        # the run, just leaves canary_loss unset so the chart skips this iter.
        canary_loss: float | None = None
        try:
            canary_loss = backend.run_canary_eval(
                run, dataset_dir, adapter_dir, run_dir, env
            )
        except Exception as e:
            log.warning("Run #%s: canary eval crashed: %s", run_id, e)

        # Phase R — remote workers ship the adapter back to the API host.
        if transfer.remote_mode() and not transfer.upload_adapter(
            run_id, adapter_dir, api_url
        ):
            log.warning(
                "Run #%s: adapter upload failed — adapter remains only on "
                "this worker at %s",
                run_id,
                adapter_dir,
            )

        patch_fields: dict[str, Any] = {
            "status": "completed",
            "adapter_path": str(adapter_dir),
            "final_train_loss": final_train_loss,
            "final_val_loss": final_val_loss,
        }
        if canary_loss is not None:
            # Persist on the Run row so the existing CanaryDriftChart picks it up,
            # and also emit as a step metric so per-run views (and any future
            # time-series plot) can see it.
            patch_fields["canary_loss"] = canary_loss
            _post_metric(api_url, run_id, run["iters"], "canary_loss", canary_loss)

        _patch_run(api_url, run_id, **patch_fields)

        # Phase D — upload artifacts to Ozone when SLM_FORGE_STORAGE=s3.
        try:
            from packages.storage_sync import sync_run_artifacts

            sync_run_artifacts(
                run_id,
                tenant_id=run.get("tenant_id"),
            )
        except Exception:
            log.exception("Run #%s: S3 artifact sync failed", run_id)
    else:
        msg = (
            f"Training process ({backend.name}) exited with code "
            f"{proc.returncode}. See {log_path}"
        )
        log.error("Run #%s: %s", run_id, msg)
        _patch_run(api_url, run_id, status="failed", error_message=msg)
