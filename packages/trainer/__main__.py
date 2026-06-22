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
import socket
import sys
import time

import httpx

from packages.trainer._env import load_project_env

# Load .env before anything else so HF_TOKEN (and any other secrets) reach the
# training subprocess, which inherits this process's os.environ. Gated HF repos
# (Gemma, Llama) 401 without it.
load_project_env()

from packages._api_client import install as install_service_auth  # noqa: E402
from packages._log_context import bind as _bind_log_ctx  # noqa: E402
from packages._log_context import reset as _reset_log_ctx  # noqa: E402
from packages._logging import setup_worker_logging  # noqa: E402
from packages.ratchet.heartbeat import start_heartbeat  # noqa: E402
from packages.trainer.backends import get_backend  # noqa: E402
from packages.trainer.runner import run_training_job  # noqa: E402

# Patch httpx so every request from this process carries X-Service-Token.
# Must run before any httpx call elsewhere in the package.
install_service_auth()

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
_log_path = setup_worker_logging("trainer")
log = logging.getLogger("trainer.worker")
log.info("Logging to %s", _log_path)

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def claim_next_run(backend_name: str) -> dict | None:
    """Phase R — atomic, backend-aware claim (replaces the GET+PATCH pickup).

    The server transitions the run queued→running with a compare-and-swap,
    so multiple workers (Mac + A100 boxes) can poll the same API safely.
    """
    try:
        r = httpx.post(
            f"{API_URL}/api/v1/runs/claim",
            json={"backend": backend_name, "worker_id": WORKER_ID},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()  # a Run dict, or None when the queue is empty
    except Exception as e:
        log.warning("Claim request failed: %s", e)
        return None


def main() -> int:
    # Phase O — resolve the training backend once at startup (fail fast on
    # an unknown SLM_FORGE_TRAINER_BACKEND value).
    try:
        backend = get_backend()
    except ValueError as e:
        log.error("%s", e)
        return 1

    log.info(
        "Trainer worker starting (API=%s, poll=%.1fs, backend=%s)",
        API_URL, POLL_INTERVAL, backend.name,
    )

    # Health check: wait for API to be reachable
    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            start_heartbeat(API_URL, worker="trainer")
            break
        except Exception:
            if attempt == 0:
                log.info("Waiting for API at %s...", API_URL)
            time.sleep(2)
    else:
        log.error("API never came up at %s. Is 'make dev' running?", API_URL)
        return 1

    log.info("Ready. Polling for queued runs every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    while True:
        try:
            run = claim_next_run(backend.name)
            if run is None:
                time.sleep(POLL_INTERVAL)
                continue

            log.info("Claimed run #%s as %s (dataset=%s, model=%s, method=%s)",
                     run["id"], WORKER_ID, run["dataset"], run["base_model"], run["method"])
            # Bind run_id into log context so every line emitted by
            # run_training_job (and anything it transitively logs) carries
            # the correlation ID when JSON logging is on.
            _tokens = _bind_log_ctx(run_id=run["id"])
            try:
                run_training_job(run, api_url=API_URL, backend=backend)
            finally:
                _reset_log_ctx(_tokens)

        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:
            log.exception("Unexpected error in worker loop: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    # PR-A — top-level wrapper. See packages/exporter/__main__.py for rationale.
    try:
        sys.exit(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as _exc:
        try:
            from packages.error_responder import capture as _capture

            _capture.report_exception_sync(_exc, source="trainer")
            _capture.flush(timeout=30)
        except Exception:
            pass
        raise
