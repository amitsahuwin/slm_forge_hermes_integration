"""Export worker — polls /api/v1/exports for queued jobs and processes them."""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from packages._log_context import bind as _bind_log_ctx
from packages._log_context import reset as _reset_log_ctx
from packages._logging import setup_worker_logging
from packages.exporter.pipeline import _check_tools, run_export_job
from packages.ratchet.heartbeat import start_heartbeat

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
_log_path = setup_worker_logging("exporter")
log = logging.getLogger("exporter.worker")
log.info("Logging to %s", _log_path)

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(
            f"{API_URL}/api/v1/exports",
            params={"status": "queued", "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[-1] if rows else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Exporter worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            start_heartbeat(API_URL, worker="exporter")
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API...")
            time.sleep(2)
    else:
        log.error("API never came up.")
        return 1

    try:
        quantize_bin, convert_script = _check_tools()
        log.info("llama-quantize: %s", quantize_bin)
        log.info("convert script: %s", convert_script)
        log.info("torch: verified importable")
    except RuntimeError as e:
        log.error("Pre-flight failed: %s", e)
        return 1

    log.info("Ready. Polling for queued exports every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

    while True:
        try:
            export = fetch_next_queued()
            if export is None:
                time.sleep(POLL_INTERVAL)
                continue
            log.info("Picked up export #%s (run=%s)", export["id"], export["run_id"])
            # Bind run_id so per-export lines correlate back to the run.
            _tokens = _bind_log_ctx(run_id=export["run_id"])
            try:
                run_export_job(export, api_url=API_URL)
            finally:
                _reset_log_ctx(_tokens)
        except KeyboardInterrupt:
            log.info("Stopping.")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Error: %s", e)
            if export:
                try:
                    httpx.patch(
                        f"{API_URL}/api/v1/exports/{export['id']}",
                        json={"status": "failed", "error_message": str(e)[:500]},
                        timeout=10,
                    )
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
