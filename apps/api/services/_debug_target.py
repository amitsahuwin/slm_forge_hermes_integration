# NO_AUTOFIX — intentional crash surface for the diagnostic endpoint.
#
# The auto-fix loop must NOT attempt to "fix" this file: it exists to
# *trigger* the dispatcher under operator control (POST /api/v1/admin/__debug__/raise),
# so the operator can verify capture + dispatcher + preflight wiring end-to-end.
# The ``# NO_AUTOFIX`` marker above is read by ``preflight()`` and short-circuits
# the SDK invocation, leaving an ``AutoFixAttempt`` row at ``status='rejected'``
# with reason "NO_AUTOFIX directive" — exactly the visible signal we want.
"""Controlled crash helper for the dev-only ``__debug__/raise`` endpoint."""
from __future__ import annotations

import builtins

_ALLOWED_EXC_TYPES = {
    "ValueError",
    "TypeError",
    "RuntimeError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
    "AssertionError",
}


def raise_for_diagnostic(exc_type: str, message: str) -> None:
    """Raise ``exc_type`` with ``message`` from a stable call frame.

    Anything not in the allowlist falls through to ``RuntimeError`` so a
    pasted typo doesn't 500 on dispatch (and so the surface area exposed
    to an authenticated admin caller is bounded).
    """
    if exc_type not in _ALLOWED_EXC_TYPES:
        raise RuntimeError(f"diagnostic crash (unknown type {exc_type!r}): {message}")
    cls = getattr(builtins, exc_type)
    raise cls(message)
