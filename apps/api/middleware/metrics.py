"""Prometheus HTTP middleware.

Records two metrics for every served request:
  * ``slmforge_http_requests_total{method, route, status}``
  * ``slmforge_http_request_duration_seconds{method, route}``

The ``route`` label uses the matched FastAPI route template
(``/api/v1/runs/{run_id}``) rather than the raw path, so cardinality stays
bounded. Requests that don't match a route (404s, OPTIONS to nowhere) are
labelled ``route="__unmatched__"``.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.api.routers.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS_TOTAL


def _route_template(request: Request) -> str:
    """Return the FastAPI route template for this request, or sentinel."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "__unmatched__"


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Count requests + observe latency in Prometheus."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        method = request.method
        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = _route_template(request)
            # Skip the /metrics route itself to keep the histogram clean —
            # Prometheus pulling its own metrics shouldn't show up as user
            # traffic.
            if route != "/metrics":
                HTTP_REQUESTS_TOTAL.labels(
                    method=method, route=route, status=str(status_code)
                ).inc()
                HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(
                    elapsed
                )
