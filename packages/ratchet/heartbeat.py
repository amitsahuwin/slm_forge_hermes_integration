"""Background heartbeat thread for the ratchet worker.

POSTs to /api/v1/hermes/heartbeat every `interval` seconds so the Dashboard
HermesStatusCard can show worker liveness. Failures are swallowed and logged
at WARNING level — the worker never dies because the API is briefly down.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

log = logging.getLogger("ratchet.heartbeat")

WORKER_NAME = "ratchet"
WORKER_VERSION = "0.6.0"


def _heartbeat_loop(api_url: str, interval: float) -> None:
    endpoint = f"{api_url.rstrip('/')}/api/v1/hermes/heartbeat"
    payload = {"worker": WORKER_NAME, "version": WORKER_VERSION}
    log.info("Heartbeat thread started → %s every %.1fs", endpoint, interval)

    while True:
        try:
            r = httpx.post(endpoint, json=payload, timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Heartbeat POST failed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.warning("Heartbeat unexpected error: %s", e)
        time.sleep(interval)


def start_heartbeat(api_url: str, interval: float = 10.0) -> threading.Thread:
    """Spawn a daemon thread that emits heartbeats. Returns the thread handle."""
    t = threading.Thread(
        target=_heartbeat_loop,
        args=(api_url, interval),
        name="ratchet-heartbeat",
        daemon=True,
    )
    t.start()
    return t
