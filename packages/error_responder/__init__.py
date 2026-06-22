"""PR-A / Workstream 2 — self-healing error reporter.

Public surface (kept narrow on purpose — the rest of the package is
implementation detail):

  - ``capture.report_exception``       (async API path)
  - ``capture.report_exception_sync``  (worker path)
  - ``capture.flush``                  (graceful shutdown drain)

The reporter never raises. Every failure mode degrades silently to
stderr so the package can be installed on a critical hot path without
risking a cascade.
"""
from __future__ import annotations

from packages.error_responder import reporter as capture

__all__ = ["capture"]
