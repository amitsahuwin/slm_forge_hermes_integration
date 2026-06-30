"""Phase D — guards on scripts/wipe_clean.py.

The script's safety net is the ``SLM_FORGE_WIPE_CONFIRM=YES`` env var;
this test pins it. Full destructive-path coverage is exercised by the
manual cutover; we don't truncate the test DB here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_refuses_without_confirm_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SLM_FORGE_WIPE_CONFIRM", raising=False)
    result = subprocess.run(
        [sys.executable, "scripts/wipe_clean.py"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 2
    assert "SLM_FORGE_WIPE_CONFIRM=YES" in result.stderr


def test_refuses_with_wrong_confirm_value(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_WIPE_CONFIRM", "yes")  # lower-case
    result = subprocess.run(
        [sys.executable, "scripts/wipe_clean.py"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**dict(__import__("os").environ), "SLM_FORGE_WIPE_CONFIRM": "yes"},
    )
    assert result.returncode == 2