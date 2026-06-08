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

log = logging.getLogger("worker.heartbeat")

WORKER_VERSION = "0.6.0"


def _heartbeat_loop(worker: str, api_url: str, interval: float) -> None:
    endpoint = f"{api_url.rstrip('/')}/api/v1/hermes/heartbeat"
    payload = {"worker": worker, "version": WORKER_VERSION}
    log.info("Heartbeat thread (%s) → %s every %.1fs", worker, endpoint, interval)

    while True:
        try:
            r = httpx.post(endpoint, json=payload, timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Heartbeat POST failed (%s): %s", worker, e)
        except Exception as e:  # noqa: BLE001
            log.warning("Heartbeat unexpected error (%s): %s", worker, e)
        time.sleep(interval)


def start_heartbeat(
    api_url: str, interval: float = 10.0, *, worker: str = "ratchet"
) -> threading.Thread:
    """Spawn a daemon thread that emits heartbeats for ``worker``.

    Backwards-compatible: ``start_heartbeat(API_URL)`` from the ratchet worker
    still works because ``worker`` defaults to "ratchet".
    """
    t = threading.Thread(
        target=_heartbeat_loop,
        args=(worker, api_url, interval),
        name=f"{worker}-heartbeat",
        daemon=True,
    )
    t.start()
    return t
