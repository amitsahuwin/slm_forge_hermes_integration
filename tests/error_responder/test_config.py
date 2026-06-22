"""PR-A — env validation fails fast at process start.

CLAUDE.md rule 23: validate config at startup; fail-fast on missing /
malformed required settings.
"""
from __future__ import annotations

import pytest

from packages.error_responder import config


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch):
    # Clear ALL reporter-related env vars so each test starts fresh.
    for key in (
        "ERROR_REPORTER_ENABLED",
        "DEPLOYMENT_MODE",
        "GITHUB_TOKEN",
        "GITHUB_REPO",
        "AUTOFIX_ENABLED",
        "AUTOFIX_MAX_ATTEMPTS_PER_FINGERPRINT_24H",
        "AUTOFIX_DEPLOY",
        "AUTOFIX_DENYLIST",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ERROR_REPORTER_STORM_THRESHOLD",
    ):
        monkeypatch.delenv(key, raising=False)
    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


def test_default_is_development_mode():
    s = config.get_settings()
    assert s.deployment_mode == "development"
    assert s.autofix_enabled is False  # default = off — safety first


def test_invalid_deployment_mode_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "staging")
    with pytest.raises(RuntimeError, match="DEPLOYMENT_MODE"):
        config.get_settings()


def test_production_without_github_token_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    # Leave GITHUB_TOKEN unset.
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        config.get_settings()


def test_production_without_github_repo_raises(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    # Run from a tmp dir that has no git remote — auto-detect fails.
    monkeypatch.chdir(tmp_path)
    # Also force the detect helper to return None (in case CWD inheritance
    # finds an outer .git).
    monkeypatch.setattr(config, "_detect_github_repo", lambda: None)
    with pytest.raises(RuntimeError, match="GITHUB_REPO"):
        config.get_settings()


def test_production_auto_detects_github_repo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(config, "_detect_github_repo", lambda: "user/repo")
    s = config.get_settings()
    assert s.github_repo == "user/repo"


def test_production_env_overrides_detected_repo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "explicit/repo")
    monkeypatch.setattr(config, "_detect_github_repo", lambda: "detected/repo")
    s = config.get_settings()
    assert s.github_repo == "explicit/repo"


def test_autofix_enabled_without_anthropic_creds_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AUTOFIX_ENABLED", "true")
    # Leave anthropic creds unset.
    with pytest.raises(RuntimeError, match="ANTHROPIC"):
        config.get_settings()


def test_settings_are_cached(monkeypatch: pytest.MonkeyPatch):
    s1 = config.get_settings()
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    # Without refresh, the cached value wins.
    s2 = config.get_settings()
    assert s1 is s2
    assert s2.deployment_mode == "development"


def test_settings_refresh_picks_up_new_env(monkeypatch: pytest.MonkeyPatch):
    config.get_settings()
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(config, "_detect_github_repo", lambda: "u/r")
    s = config.get_settings(refresh=True)
    assert s.deployment_mode == "production"


def test_denylist_substring_match():
    s = config.get_settings()
    assert config.file_in_denylist("apps/api/main.py", s) is True
    assert config.file_in_denylist("apps/api/middleware/auth.py", s) is True
    assert config.file_in_denylist("apps/api/routers/runs.py", s) is False


def test_denylist_env_overrides_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOFIX_DENYLIST", "secrets/,critical.py")
    config.reset_settings_cache()
    s = config.get_settings()
    assert config.file_in_denylist("secrets/whatever.py", s) is True
    assert config.file_in_denylist("apps/api/main.py", s) is False  # no longer denied


def test_invalid_autofix_deploy_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTOFIX_DEPLOY", "yolo")
    with pytest.raises(RuntimeError, match="AUTOFIX_DEPLOY"):
        config.get_settings()
