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
