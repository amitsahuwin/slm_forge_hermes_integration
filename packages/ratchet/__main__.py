"""Autoresearch ratchet worker. Polls API for queued sessions and runs them.

Run via:
    uv run python -m packages.ratchet

Requires:
  - The API to be reachable (make dev)
  - The trainer worker to be running (make trainer)
  - Ollama serving qwen2.5-coder:14b (make install-hermes)
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

from packages._log_context import bind as _bind_log_ctx
from packages._log_context import reset as _reset_log_ctx
from packages._logging import setup_worker_logging
from packages.ratchet.heartbeat import start_heartbeat
from packages.ratchet.hermes_bridge import healthcheck
from packages.ratchet.loop import API, run_session

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
_log_path = setup_worker_logging("ratchet")
log = logging.getLogger("ratchet.worker")
log.info("Logging to %s", _log_path)

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "3.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(
            f"{API_URL}/api/v1/sessions",
            params={"status": "queued", "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        sessions = r.json()
        return sessions[-1] if sessions else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Ratchet worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

    # Wait for API
    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            start_heartbeat(API_URL)
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API at %s...", API_URL)
            time.sleep(2)
    else:
        log.error("API never came up. Is 'make dev' running?")
        return 1

    # Verify Ollama + qwen
    ok, msg = healthcheck()
    if not ok:
        log.error("Hermes/Ollama healthcheck failed: %s", msg)
        log.error("Run 'make install-hermes' first, then retry.")
        return 1
    log.info("Hermes bridge: %s", msg)

    log.info("Ready. Polling for queued sessions every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    api = API(API_URL)
    while True:
        try:
            session = fetch_next_queued()
            if session is None:
                time.sleep(POLL_INTERVAL)
                continue
            # Bind session_id so every line in the autoresearch loop is
            # tagged for cross-service correlation.
            _tokens = _bind_log_ctx(session_id=session["id"])
            try:
                run_session(session["id"], api)
            finally:
                _reset_log_ctx(_tokens)
        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Session orchestration failed: %s", e)
            try:
                httpx.patch(
                    f"{API_URL}/api/v1/sessions/{session['id']}",
                    json={"status": "failed", "error_message": str(e)[:500]},
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
