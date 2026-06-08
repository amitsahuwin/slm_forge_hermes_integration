"""Host trainer worker.

Polls the SLM-Forge API for queued runs, picks them up one at a time,
invokes `mlx_lm.lora` as a subprocess, and streams metrics back over HTTP.

Run with:
    uv run python -m packages.trainer

This process MUST run on the host (not in Docker) so MLX-LM can access
Apple Metal/MPS. Docker on macOS has no GPU passthrough.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

from packages._logging import setup_worker_logging
from packages.ratchet.heartbeat import start_heartbeat
from packages.trainer.runner import run_training_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
_log_path = setup_worker_logging("trainer")
log = logging.getLogger("trainer.worker")
log.info("Logging to %s", _log_path)

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/api/v1/runs", params={"status": "queued", "limit": 1}, timeout=5)
        r.raise_for_status()
        runs = r.json()
        return runs[-1] if runs else None  # oldest queued
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Trainer worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

    # Health check: wait for API to be reachable
    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            start_heartbeat(API_URL, worker="trainer")
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API at %s...", API_URL)
            time.sleep(2)
    else:
        log.error("API never came up at %s. Is 'make dev' running?", API_URL)
        return 1

    log.info("Ready. Polling for queued runs every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    while True:
        try:
            run = fetch_next_queued()
            if run is None:
                time.sleep(POLL_INTERVAL)
                continue

            log.info("Picked up run #%s (dataset=%s, model=%s, method=%s)",
                     run["id"], run["dataset"], run["base_model"], run["method"])
            run_training_job(run, api_url=API_URL)

        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error in worker loop: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
