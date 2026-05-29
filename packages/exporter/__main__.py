"""Export worker — polls /api/v1/exports for queued jobs and processes them.

Run via:
    uv run python -m packages.exporter

Like the trainer, this must run on host (not Docker) because mlx_lm.fuse
needs Apple Metal access.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

# Auto-load .env from project root
try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from packages.exporter.pipeline import run_export_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("exporter.worker")

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
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API at %s...", API_URL)
            time.sleep(2)
    else:
        log.error("API never came up. Is 'make dev' running?")
        return 1

    # Pre-flight: verify llama.cpp tools BEFORE polling, so the user gets a
    # clean error rather than a queued export silently failing.
    from packages.exporter.pipeline import _check_tools
    try:
        q, c = _check_tools()
        log.info("llama-quantize: %s", q)
        log.info("convert_hf_to_gguf.py: %s", c)
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
            run_export_job(export, api_url=API_URL)
        except KeyboardInterrupt:
            log.info("Stopping (KeyboardInterrupt).")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
