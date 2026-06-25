"""Contract test — every worker ``__main__.py`` must wrap ``main()`` in a
``try/except BaseException → report_exception_sync → flush → raise`` block.

This is a *source-level* assertion. A behavioral test would have to spawn
each worker via ``runpy``, force ``main()`` to raise, and inspect side
effects — heavy and brittle for something whose actual surface is a
five-line wrapper. The structural check below catches the regression we
actually care about: someone refactoring the entrypoint and silently
dropping the reporter hook.

If you legitimately move the wrapper elsewhere, update this test to point
at the new home rather than deleting it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# (module path, source label) pairs. Source label is the value passed to
# report_exception_sync(source=...) so the AutoFixAttempt row carries the
# right component tag.
WORKER_ENTRYPOINTS = [
    ("packages/trainer/__main__.py", "trainer"),
    ("packages/ratchet/__main__.py", "ratchet"),
    ("packages/exporter/__main__.py", "exporter"),
]


@pytest.mark.parametrize("path,source_label", WORKER_ENTRYPOINTS)
def test_worker_entrypoint_calls_report_exception_sync(path: str, source_label: str):
    src = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert "report_exception_sync" in src, (
        f"{path} no longer imports report_exception_sync — the worker would "
        f"crash silently without recording an AutoFixAttempt row."
    )
    assert f'source="{source_label}"' in src, (
        f"{path} must call report_exception_sync(source=\"{source_label}\") so "
        f"the AutoFixAttempt row carries the correct component tag."
    )
    # The wrapper must re-raise; we don't want a swallowed crash that pretends
    # the worker exited cleanly with code 0.
    assert "raise" in src.split("report_exception_sync")[-1], (
        f"{path} must re-raise after reporting — silent exits hide failures."
    )


def test_api_main_registers_error_capture_middleware_and_handler():
    """The FastAPI side mirrors the worker hook surface."""
    src = (REPO_ROOT / "apps/api/main.py").read_text(encoding="utf-8")
    assert "ErrorCaptureMiddleware" in src
    assert "start_dispatcher" in src
    assert "@app.exception_handler(Exception)" in src
    assert 'report_exception' in src
