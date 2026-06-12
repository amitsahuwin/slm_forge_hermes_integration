"""``GET /metrics`` — Prometheus text-format scrape endpoint.

Two flavours of metrics live here:

1. **HTTP counters/histograms** — populated by ``PrometheusMiddleware`` as
   requests flow through.
2. **Gauges** — refreshed inline on each scrape by sampling SQLite.
   SQLite reads are cheap (single-digit ms for the tables we touch) and
   running them on demand means we don't need a background task.

Prometheus default scrape interval is 15s, so the refresh is essentially
free for any sane query workload.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text
from sqlmodel import Session, select

log = logging.getLogger(__name__)

router = APIRouter()

# Use the default global registry — prometheus_client wires everything to it
# automatically, and we get the standard process/python collectors for free.
REGISTRY = CollectorRegistry(auto_describe=True)

# --- HTTP request metrics (filled by PrometheusMiddleware) -----------------

HTTP_REQUESTS_TOTAL = Counter(
    "slmforge_http_requests_total",
    "Total HTTP requests served by the API.",
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_DURATION = Histogram(
    "slmforge_http_request_duration_seconds",
    "Latency of HTTP requests served by the API.",
    labelnames=("method", "route"),
    # Buckets tuned for a local API — most calls < 100 ms, training-status
    # SSE openers can stretch to a second.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# --- Domain counters / gauges ---------------------------------------------

RUNS_TOTAL = Counter(
    "slmforge_runs_total",
    "Total fine-tuning runs partitioned by terminal status.",
    labelnames=("status",),
)

ACTIVE_RUNS = Gauge(
    "slmforge_active_runs",
    "Currently running fine-tuning runs (status=running).",
)

WORKER_HEARTBEAT_AGE = Gauge(
    "slmforge_worker_heartbeat_age_seconds",
    "Seconds since each worker last sent a heartbeat.",
    labelnames=("worker",),
)

DATASET_COUNT = Gauge(
    "slmforge_dataset_count",
    "Number of datasets registered on the filesystem.",
)

CHAT_MESSAGES_TOTAL = Counter(
    "slmforge_chat_messages_total",
    "Total chat messages persisted, partitioned by role.",
    labelnames=("role",),
)


def _refresh_gauges_from_db() -> None:
    """Sample SQLite tables to (re)set our Gauges before serialising.

    Failure is logged + swallowed — `/metrics` must never 500 on a DB
    hiccup, otherwise Prometheus marks the target down and the dashboard
    goes dark.
    """
    try:
        from apps.api.models.heartbeat import WorkerHeartbeat
        from apps.api.services.db import engine
    except Exception:  # noqa: BLE001
        log.debug("metrics: DB modules unavailable", exc_info=True)
        return

    try:
        with Session(engine) as db:
            # Active runs. `db.exec(text(...)).one()` returns a Row (not a
            # tuple, not an int), but Row supports both `[0]` indexing and
            # `_mapping.values()`. Pull the single column robustly.
            row = db.exec(
                text("SELECT COUNT(*) FROM runs WHERE status = 'running'")
            ).one()
            try:
                count = row[0]  # tuple / Row / sqlmodel Row
            except (TypeError, KeyError, IndexError):
                count = row     # raw scalar (very old SQLAlchemy / dialect quirk)
            ACTIVE_RUNS.set(int(count))

            # Heartbeat ages — one Gauge sample per worker row.
            now = datetime.now(UTC)
            hbs = db.exec(select(WorkerHeartbeat)).all()
            seen: set[str] = set()
            for hb in hbs:
                last = hb.last_seen
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                age = (now - last).total_seconds()
                WORKER_HEARTBEAT_AGE.labels(worker=hb.worker).set(age)
                seen.add(hb.worker)
            # Workers we know about but have never seen → leave their Gauge
            # alone. Prometheus will keep the last-known value, which is the
            # honest answer ("we have no fresh data").

            # Dataset count — best-effort filesystem walk.
            try:
                from pathlib import Path

                data_dir = Path("/app/data/datasets")
                if data_dir.exists():
                    DATASET_COUNT.set(
                        sum(1 for p in data_dir.iterdir() if p.is_dir())
                    )
            except Exception:  # noqa: BLE001
                log.debug("metrics: dataset count failed", exc_info=True)
    except Exception:  # noqa: BLE001
        log.warning("metrics: gauge refresh failed", exc_info=True)


@router.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    """Return the latest Prometheus text exposition."""
    _refresh_gauges_from_db()
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
