"""PR-A — capture exceptions that escape middleware *before* they reach
the route handler (validation errors, AuthMiddleware bugs, etc.).

The ``@app.exception_handler`` decorator only fires for exceptions from
inside route handlers — middleware exceptions bypass it. This middleware
sits just inside CORS so it sees everything below it.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from packages.error_responder import capture

log = logging.getLogger("api.error_capture")


class ErrorCaptureMiddleware(BaseHTTPMiddleware):
    """Wraps ``call_next`` so any uncaught middleware/handler exception is
    reported to the error-responder and then re-raised so Starlette can
    still produce its 500 response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        try:
            return await call_next(request)
        except Exception as exc:
            try:
                capture.report_exception(exc, source="api.middleware")
            except Exception as cap_exc:
                log.error("error_responder itself raised: %s", cap_exc)
            raise
