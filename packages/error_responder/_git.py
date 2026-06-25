"""Git helpers used by both the production-mode reporter (repo autodetect)
and the development-mode auto-fix flow (clean-tree / branch / worktree).

Kept thin on purpose — the heavier logic lives in ``autofix.py`` (PR-B).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("error_responder._git")


def is_git_repo(path: Path | None = None) -> bool:
    path = path or Path.cwd()
    if shutil.which("git") is None:
        return False
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            cwd=str(path),
            timeout=2,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def current_branch(path: Path | None = None) -> str | None:
    """Return the current branch name, or ``None`` on detached HEAD / no repo."""
    path = path or Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(path),
            timeout=2,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if not out or out == "HEAD":
        return None
    return out


def is_working_tree_clean(path: Path | None = None) -> bool:
    """``git status --porcelain`` empty → clean."""
    path = path or Path.cwd()
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(path),
            timeout=5,
        ).stdout
        return out.strip() == ""
    except (subprocess.SubprocessError, OSError):
        return False


def push_branch(
    branch: str,
    *,
    cwd: Path | None = None,
    remote: str = "origin",
    timeout: int = 30,
) -> tuple[bool, str | None]:
    """Push a local branch to ``remote`` and set its upstream.

    Returns ``(True, None)`` on success, ``(False, error_message)`` on
    failure. Never raises — the caller (auto-fix orchestrator) needs a
    structured outcome so it can mark the AutoFixAttempt row ``failed``
    without a stacktrace leaking past it.
    """
    path = cwd or Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "push", "-u", remote, branch],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(path),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"git push timed out after {timeout}s"
    except OSError as e:
        return False, f"git push could not be invoked: {e}"
    if proc.returncode == 0:
        return True, None
    err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
    return False, err
