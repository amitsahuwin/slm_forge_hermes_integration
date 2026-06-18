"""Phase U — the trainer worker loads project .env so HF_TOKEN reaches the
gated-model download in the CUDA subprocess."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from packages.trainer._env import PROJECT_ROOT, load_project_env


def test_project_root_points_at_repo_root() -> None:
    # _env.py lives at packages/trainer/_env.py → repo root is parents[2].
    assert (PROJECT_ROOT / "pyproject.toml").exists()


def test_load_project_env_reads_a_custom_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLM_FORGE_PHASE_U_SENTINEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SLM_FORGE_PHASE_U_SENTINEL=hf_xyz\n", encoding="utf-8")

    loaded = load_project_env(env_file)

    assert loaded is True
    assert os.environ.get("SLM_FORGE_PHASE_U_SENTINEL") == "hf_xyz"


def test_load_project_env_does_not_override_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLM_FORGE_PHASE_U_SENTINEL", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("SLM_FORGE_PHASE_U_SENTINEL=from-file\n", encoding="utf-8")

    load_project_env(env_file)

    # override=False → a value already in the environment wins.
    assert os.environ["SLM_FORGE_PHASE_U_SENTINEL"] == "already-set"


def test_load_project_env_missing_file_is_noop(tmp_path: Path) -> None:
    # dotenv returns False when the file does not exist; must not raise.
    assert load_project_env(tmp_path / "nonexistent.env") is False
