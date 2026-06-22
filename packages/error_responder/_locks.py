"""Tiny file-lock helper for the single-in-flight auto-fix mutex (PR-B).

Used as a context manager:

    with try_acquire(LOCK_PATH) as ok:
        if not ok:
            # Another worker is mid-fix; defer.
            return
        ...
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

DEFAULT_LOCK_PATH = Path("/tmp/slm_forge_autofix.lock")


@contextmanager
def try_acquire(path: Path = DEFAULT_LOCK_PATH):
    """Non-blocking ``fcntl.flock`` on the given path.

    Yields ``True`` if the lock was acquired (caller MUST do the work),
    ``False`` if another process holds it. Releases automatically on exit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
