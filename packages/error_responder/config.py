"""Configuration + env-validation for the error reporter.

Fail-fast at startup per CLAUDE.md rule 23. Cached singleton via
``get_settings()``; tests reset it via ``reset_settings_cache()``.

All env names sit under three prefixes:

  DEPLOYMENT_MODE          production | development
  GITHUB_*                 GitHub issue mode requirements
  AUTOFIX_*                Development-mode auto-fix knobs (PR-B)
  ERROR_REPORTER_*         Cross-cutting (storm cap, SDK caps)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("error_responder.config")


# Default denylist — files the auto-fix loop must NEVER edit. Comma-separated
# in env; substring match against the project-relative file path.
_DEFAULT_DENYLIST = (
    "apps/api/main.py,"
    "apps/api/services/db.py,"
    "apps/api/middleware/,"
    "packages/_logging.py,"
    "apps/api/services/model_catalog.py,"
    "packages/error_responder/"
)


@dataclass(frozen=True)
class ErrorReporterSettings:
    """Frozen snapshot of the env at process start.

    Frozen so the dispatcher can't be reconfigured mid-flight by a stray
    ``os.environ.pop`` call. Tests reset via ``reset_settings_cache``.
    """

    enabled: bool
    deployment_mode: str  # "production" | "development"

    # Production mode
    github_token: str | None
    github_repo: str | None  # "owner/repo"

    # Development mode
    autofix_enabled: bool
    autofix_max_per_fp_24h: int
    autofix_deploy: str  # "pr" | "local-reload" | "auto-merge"
    autofix_denylist: tuple[str, ...]
    autofix_model: str  # forwarded into ClaudeAgentOptions(model=)

    # Anthropic SDK (consumed by claude_agent_sdk via os.environ — we just
    # validate presence here).
    anthropic_base_url: str | None
    anthropic_auth_token: str | None
    anthropic_api_key: str | None

    # Cross-cutting
    storm_threshold: int
    sdk_max_turns: int
    sdk_timeout_seconds: int
    sdk_hourly_cap: int

    # Diagnostics
    project_root: Path = field(default_factory=lambda: Path.cwd())


_CACHE: ErrorReporterSettings | None = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw not in ("false", "0", "no", "")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        log.warning("invalid int for %s — falling back to %d", name, default)
        return default


def _detect_github_repo() -> str | None:
    """Parse ``owner/repo`` from ``git remote get-url origin``.

    Supports both ssh (``git@github.com:owner/repo.git``) and https
    (``https://github.com/owner/repo.git`` or ``…/repo``) remotes.
    Returns ``None`` if git isn't available or no origin is configured.
    """
    if shutil.which("git") is None:
        return None
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if not out:
        return None

    if out.startswith("git@"):
        # git@github.com:owner/repo.git
        _, _, path = out.partition(":")
    elif "://" in out:
        # https://github.com/owner/repo.git
        path = out.split("://", 1)[1].split("/", 1)[1] if "/" in out.split("://", 1)[1] else ""
    else:
        return None

    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip().strip("/")
    parts = path.split("/")
    if len(parts) < 2 or not parts[-2] or not parts[-1]:
        return None
    # Take the LAST two segments — handles ``github.com/owner/repo`` reliably.
    return f"{parts[-2]}/{parts[-1]}"


def get_settings(*, refresh: bool = False) -> ErrorReporterSettings:
    """Return the cached settings; build + validate on first call.

    Raises ``RuntimeError`` on missing-required configuration so the
    process refuses to start instead of silently degrading.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    enabled = _env_bool("ERROR_REPORTER_ENABLED", True)
    deployment_mode = os.environ.get("DEPLOYMENT_MODE", "development").strip().lower()
    if deployment_mode not in ("production", "development"):
        raise RuntimeError(
            f"DEPLOYMENT_MODE must be 'production' or 'development' (got {deployment_mode!r})"
        )

    github_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    github_repo = os.environ.get("GITHUB_REPO", "").strip() or None
    if github_repo is None:
        github_repo = _detect_github_repo()

    autofix_enabled = _env_bool("AUTOFIX_ENABLED", False)
    autofix_max_per_fp_24h = _env_int("AUTOFIX_MAX_ATTEMPTS_PER_FINGERPRINT_24H", 3)
    autofix_deploy = os.environ.get("AUTOFIX_DEPLOY", "auto-commit-reload").strip().lower()
    if autofix_deploy not in ("auto-commit-reload", "pr", "local-reload", "auto-merge"):
        raise RuntimeError(
            f"AUTOFIX_DEPLOY must be one of "
            "{auto-commit-reload, pr, local-reload, auto-merge} "
            f"(got {autofix_deploy!r})"
        )

    raw_denylist = os.environ.get("AUTOFIX_DENYLIST", _DEFAULT_DENYLIST)
    autofix_denylist = tuple(p.strip() for p in raw_denylist.split(",") if p.strip())

    autofix_model = os.environ.get(
        "AUTOFIX_MODEL", "anthropic/claude-3-5-sonnet-20241022"
    ).strip()

    anth_base = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or None
    anth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip() or None
    anth_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None

    storm_threshold = _env_int("ERROR_REPORTER_STORM_THRESHOLD", 10)
    sdk_max_turns = _env_int("ERROR_REPORTER_SDK_MAX_TURNS", 8)
    sdk_timeout_seconds = _env_int("ERROR_REPORTER_SDK_TIMEOUT_SECONDS", 180)
    sdk_hourly_cap = _env_int("ERROR_REPORTER_SDK_HOURLY_CAP", 100)

    # ── Fail-fast validation ───────────────────────────────────────────
    if enabled and deployment_mode == "production" and not github_token:
        raise RuntimeError(
            "DEPLOYMENT_MODE=production requires GITHUB_TOKEN in the env"
        )
    if enabled and deployment_mode == "production" and not github_repo:
        raise RuntimeError(
            "DEPLOYMENT_MODE=production needs GITHUB_REPO (env or `git remote get-url origin`)"
        )
    if autofix_enabled and not (anth_token or anth_key):
        raise RuntimeError(
            "AUTOFIX_ENABLED=true requires ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY"
        )
    if autofix_enabled:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "AUTOFIX_ENABLED=true but the `claude-agent-sdk` package isn't installed"
            ) from e

    _CACHE = ErrorReporterSettings(
        enabled=enabled,
        deployment_mode=deployment_mode,
        github_token=github_token,
        github_repo=github_repo,
        autofix_enabled=autofix_enabled,
        autofix_max_per_fp_24h=autofix_max_per_fp_24h,
        autofix_deploy=autofix_deploy,
        autofix_denylist=autofix_denylist,
        autofix_model=autofix_model,
        anthropic_base_url=anth_base,
        anthropic_auth_token=anth_token,
        anthropic_api_key=anth_key,
        storm_threshold=storm_threshold,
        sdk_max_turns=sdk_max_turns,
        sdk_timeout_seconds=sdk_timeout_seconds,
        sdk_hourly_cap=sdk_hourly_cap,
    )
    return _CACHE


def reset_settings_cache() -> None:
    """Test helper — drop the cached singleton so the next ``get_settings``
    reads the current env. Equivalent of restarting the process."""
    global _CACHE
    _CACHE = None


def file_in_denylist(file_path: str, settings: ErrorReporterSettings | None = None) -> bool:
    """Substring match — denylist entries can be exact paths or directory
    prefixes (e.g. ``apps/api/middleware/``)."""
    s = settings or get_settings()
    return any(entry and entry in file_path for entry in s.autofix_denylist)
