"""Diagnostic crash target — ``apps/api/services/_debug_target``.

The endpoint wrapper (``POST /api/v1/admin/__debug__/raise``) is a 3-line
``requires("update","setting") + dev-mode-gate + delegate`` shim; the
substantive logic is in the helper. These tests pin the contract:

  - Allowed exception types raise verbatim with the given message.
  - Unknown types fall through to ``RuntimeError`` (bounded surface area).
  - The file itself carries the ``# NO_AUTOFIX`` marker so ``preflight()``
    skips the SDK on a real fired crash — exactly the visible
    ``status=rejected`` signal the operator wants to see at /autofix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.services import _debug_target


def test_raises_valueerror_with_message():
    with pytest.raises(ValueError, match="boom-1"):
        _debug_target.raise_for_diagnostic("ValueError", "boom-1")


def test_raises_runtime_error():
    with pytest.raises(RuntimeError, match="boom-2"):
        _debug_target.raise_for_diagnostic("RuntimeError", "boom-2")


def test_unknown_type_falls_through_to_runtime_error():
    with pytest.raises(RuntimeError, match="unknown type 'OSError'"):
        _debug_target.raise_for_diagnostic("OSError", "anything")


def test_debug_target_file_carries_no_autofix_marker():
    """preflight() greps for ``# NO_AUTOFIX`` at the top of the source.
    Without it, the SDK would actually try to "fix" the intentional raise,
    burning a sandbox commit on every diagnostic call."""
    src = Path(_debug_target.__file__).read_text(encoding="utf-8")
    assert "# NO_AUTOFIX" in src, (
        "The diagnostic target must carry # NO_AUTOFIX so preflight() rejects "
        "the SDK invocation. Removing this marker is a regression."
    )


def test_all_allowed_types_raise_correctly():
    for exc_type in (
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "IndexError",
        "ZeroDivisionError",
        "AssertionError",
    ):
        with pytest.raises(BaseException) as excinfo:
            _debug_target.raise_for_diagnostic(exc_type, f"test-{exc_type}")
        assert type(excinfo.value).__name__ == exc_type
