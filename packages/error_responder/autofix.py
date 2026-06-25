"""PR-B — dev-mode auto-fix orchestration.

End-to-end pipeline driven from ``reporter._dispatch_dev``:

  preflight   → all gates must pass; any failure → AutoFixAttempt.rejected
  sandbox     → create `auto-fix/<fp12>-<utcstamp>` branch in-place
  invoke_sdk  → drive ClaudeSDKClient under wait_for(timeout)
  verify      → test-quality gate (FAIL pre-fix, PASS post-fix)
                + targeted pytest + full suite + ruff/mypy (non-blocking)
  deploy      → git commit on the sandbox branch (NEVER touches main —
                uvicorn --reload picks up the source-file change via
                watchfiles; workers emit autofix.restart_required)
  record      → AutoFixAttempt status transitions
                proposed → applied → verified → deployed
                                              ↘ failed

NONE of these helpers raise into the caller. The orchestrator returns an
``AutoFixOutcome`` describing what happened so ``reporter.py`` can decide
whether to also fall back to the GitHub-issue path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.error_responder import _git, _locks
from packages.error_responder import config as _config

log = logging.getLogger("error_responder.autofix")


@dataclass
class AutoFixOutcome:
    """What happened end-to-end. The orchestrator NEVER raises — every
    pipeline failure is captured here."""

    status: str  # "deployed" | "rejected" | "failed"
    reason: str = ""
    branch: str | None = None
    test_path: str | None = None
    source_files: list[str] = field(default_factory=list)
    diff: str | None = None  # captured `git diff` snapshot (capped)
    pr_url: str | None = None  # populated only when AUTOFIX_DEPLOY=pr


# ── Preflight ─────────────────────────────────────────────────────────


def _attempts_in_24h(fingerprint: str) -> int:
    """Count AutoFixAttempt rows for ``fingerprint`` in the last 24h."""
    try:
        from sqlmodel import Session, select

        from apps.api.models.autofix import AutoFixAttempt
        from apps.api.services.db import engine

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        with Session(engine) as db:
            rows = db.exec(
                select(AutoFixAttempt).where(
                    AutoFixAttempt.fingerprint == fingerprint,
                    AutoFixAttempt.created_at >= cutoff,
                )
            ).all()
            return len(rows)
    except Exception as e:
        log.debug("attempts_in_24h fallback to 0 (%s)", e)
        return 0


def _file_opts_out(repo_root: Path, file_path: str) -> bool:
    """File contains a top-of-file ``# NO_AUTOFIX`` directive."""
    full = repo_root / file_path
    if not full.exists():
        return False
    try:
        head = full.read_text(encoding="utf-8", errors="replace")[:4_096]
    except OSError:
        return False
    return "# NO_AUTOFIX" in head


def preflight(
    *,
    fingerprint: str,
    file_target: str | None,
    repo_root: Path,
) -> tuple[bool, str]:
    """Run every safety gate. Returns ``(ok, reason)``.

    The caller MUST treat ``ok=False`` as a terminal state — record
    ``status=rejected`` and degrade to the GitHub-issue path.
    """
    settings = _config.get_settings()

    if not settings.autofix_enabled:
        return False, "AUTOFIX_ENABLED is false"
    if settings.deployment_mode != "development":
        return False, "AUTOFIX runs in development mode only"

    # SDK + creds must be wired.
    from packages.error_responder import sdk_client as _sdk

    if not _sdk.sdk_available():
        return False, "claude_agent_sdk is not importable"
    if not _sdk.env_ready_for_sdk():
        return False, "ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY required"

    if not _git.is_git_repo(repo_root):
        return False, "no git repo at project root"

    branch = _git.current_branch(repo_root)
    if branch is None:
        return False, "detached HEAD — refusing to auto-fix"
    if branch == "main":
        return False, "current branch is main — refusing to auto-fix"

    if not _git.is_working_tree_clean(repo_root):
        return False, "working tree is dirty — refusing to auto-fix"

    if file_target is None:
        return False, "no project frame in traceback — can't locate the fix target"

    if file_target.startswith("tests/"):
        return False, f"target {file_target} is under tests/ — refusing to auto-edit tests"

    if _config.file_in_denylist(file_target, settings):
        return False, f"target {file_target} is in AUTOFIX_DENYLIST"

    if _file_opts_out(repo_root, file_target):
        return False, f"target {file_target} carries # NO_AUTOFIX"

    n = _attempts_in_24h(fingerprint)
    if n >= settings.autofix_max_per_fp_24h:
        return (
            False,
            f"attempts cap reached ({n} ≥ {settings.autofix_max_per_fp_24h}) in 24h",
        )

    return True, ""


# ── Sandbox + git helpers ─────────────────────────────────────────────


def _run_git(
    args: list[str], *, cwd: Path, timeout: float = 20.0
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout,
    )


def sandbox_branch(repo_root: Path, fp12: str) -> str | None:
    """Create + check out ``auto-fix/<fp12>-<utcstamp>`` in-place.

    Returns the branch name on success; ``None`` on failure (logged).
    Per the user's chosen deploy mode (``auto-commit-reload``) we stay on
    this branch — main is never touched.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"auto-fix/{fp12}-{stamp}"
    out = _run_git(["checkout", "-b", name], cwd=repo_root)
    if out.returncode != 0:
        log.warning("sandbox checkout -b %s failed: %s", name, out.stderr.strip())
        return None
    return name


def revert_sandbox(repo_root: Path, branch: str, original_branch: str) -> None:
    """Best-effort cleanup when verification fails.

    Restores the workspace to ``original_branch`` and deletes the sandbox.
    Failure to clean up is logged but doesn't propagate — the orchestrator
    has already recorded a ``failed`` outcome.
    """
    _run_git(["reset", "--hard", "HEAD"], cwd=repo_root)
    _run_git(["checkout", original_branch], cwd=repo_root)
    _run_git(["branch", "-D", branch], cwd=repo_root)


def capture_diff(repo_root: Path, *, max_bytes: int = 64_000) -> str:
    """Snapshot ``git diff HEAD`` for persistence in AutoFixAttempt.diff."""
    out = _run_git(["diff", "HEAD"], cwd=repo_root, timeout=30.0)
    return (out.stdout or "")[:max_bytes]


def git_commit_fix(repo_root: Path, *, fp12: str, exc_type: str, file_target: str) -> bool:
    """Stage everything on the branch and commit with a fingerprint-tagged
    message. Returns True on success."""
    _run_git(["add", "-A"], cwd=repo_root)
    msg = f"auto-fix({fp12}): {exc_type} at {file_target}"
    out = _run_git(["commit", "-m", msg], cwd=repo_root)
    if out.returncode != 0:
        log.warning("auto-fix commit failed: %s", out.stderr.strip())
        return False
    return True


# ── Verify ─────────────────────────────────────────────────────────────


@dataclass
class VerifyOutcome:
    ok: bool
    reason: str = ""
    pytest_full_passed: bool = False
    ruff_passed: bool = False
    mypy_passed: bool = False


def _run(
    cmd: list[str], *, cwd: Path, timeout: float
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout,
    )


def _stash_source_only(repo_root: Path, source_files: list[str]) -> bool:
    """Stash exactly the source-file changes (leave the new test in place)
    so we can rerun the test against the pre-fix code.

    Returns True if a stash entry was created."""
    if not source_files:
        return False
    args = ["stash", "push", "-q", "-m", "autofix:pre-fix-quality-gate", "--"]
    args.extend(source_files)
    _run_git(args, cwd=repo_root)
    # ``git stash push`` returns 0 even when nothing was stashed — we have
    # to introspect via ``git stash list``.
    listing = _run_git(["stash", "list"], cwd=repo_root)
    return "autofix:pre-fix-quality-gate" in (listing.stdout or "")


def _unstash(repo_root: Path) -> None:
    _run_git(["stash", "pop"], cwd=repo_root)


def quality_gate(
    repo_root: Path, *, test_path: str, source_files: list[str], timeout_per_test: float = 30
) -> tuple[bool, str]:
    """The non-negotiable check: rerunning the new test against PRE-fix
    code must FAIL. Then re-applying the fix and running it must PASS.

    Returns ``(ok, reason)``.
    """
    if not test_path:
        return False, "no test_path produced by SDK"
    if not source_files:
        return False, "SDK didn't edit any source files — vacuous fix"

    stashed = _stash_source_only(repo_root, source_files)
    if not stashed:
        return False, "couldn't isolate source changes for pre-fix gate"

    try:
        pre = _run(
            ["uv", "run", "pytest", test_path, "-x", "-q"],
            cwd=repo_root,
            timeout=timeout_per_test,
        )
    except subprocess.TimeoutExpired:
        _unstash(repo_root)
        return False, "pre-fix test run timed out"

    if pre.returncode == 0:
        _unstash(repo_root)
        return (
            False,
            "test passed without the fix — tautological (rejected per quality gate)",
        )

    _unstash(repo_root)

    try:
        post = _run(
            ["uv", "run", "pytest", test_path, "-x", "-q"],
            cwd=repo_root,
            timeout=timeout_per_test,
        )
    except subprocess.TimeoutExpired:
        return False, "post-fix targeted test run timed out"

    if post.returncode != 0:
        return False, "fix didn't make the test pass"

    return True, ""


def verify(
    repo_root: Path,
    *,
    test_path: str,
    source_files: list[str],
    pytest_full_timeout: float = 120.0,
) -> VerifyOutcome:
    """Run the full verification gate.

    1. Quality gate (pre-fix FAIL → post-fix PASS).
    2. Full pytest suite — must pass.
    3. ruff + mypy on touched files — recorded but non-blocking.
    """
    ok, reason = quality_gate(
        repo_root, test_path=test_path, source_files=source_files
    )
    if not ok:
        return VerifyOutcome(ok=False, reason=reason)

    try:
        full = _run(
            ["uv", "run", "pytest", "-x", "-q"],
            cwd=repo_root,
            timeout=pytest_full_timeout,
        )
    except subprocess.TimeoutExpired:
        return VerifyOutcome(ok=False, reason="full pytest timed out")

    if full.returncode != 0:
        tail = (full.stdout or full.stderr or "")[-2_000:]
        return VerifyOutcome(
            ok=False,
            reason=f"full pytest failed:\n{tail}",
        )

    # ruff/mypy — non-blocking, just record the bool so AutoFixAttempt can
    # carry the signal if we ever wire it into the row.
    try:
        ruff = _run(
            ["uv", "run", "ruff", "check", *source_files, test_path],
            cwd=repo_root,
            timeout=30.0,
        )
        ruff_passed = ruff.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ruff_passed = False
    try:
        mypy = _run(
            ["uv", "run", "mypy", *source_files, test_path],
            cwd=repo_root,
            timeout=60.0,
        )
        mypy_passed = mypy.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        mypy_passed = False

    return VerifyOutcome(
        ok=True,
        pytest_full_passed=True,
        ruff_passed=ruff_passed,
        mypy_passed=mypy_passed,
    )


# ── Persistence ───────────────────────────────────────────────────────


def _update_attempt(
    *,
    fingerprint: str,
    status: str,
    branch: str | None,
    test_path: str | None,
    file_target: str | None,
    diff: str | None,
    error_message: str,
    exc_type: str,
    pr_url: str | None = None,
) -> int | None:
    """Insert an AutoFixAttempt row for this run. Best-effort."""
    try:
        from sqlmodel import Session as _Session

        from apps.api.models.autofix import AutoFixAttempt
        from apps.api.services.db import engine
        from apps.api.services.tenant import current_tenant

        with _Session(engine) as db:
            row = AutoFixAttempt(
                fingerprint=fingerprint,
                mode="development",
                source="api",
                error_type=exc_type,
                error_message=error_message[:2_000],
                file_target=file_target,
                branch=branch,
                test_path=test_path,
                status=status,
                diff=diff,
                pr_url=pr_url,
                tenant_id=current_tenant(),
                completed_at=datetime.now(UTC),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
    except Exception as e:
        log.debug("AutoFixAttempt persistence skipped (%s)", e)
        return None


# ── Public entry ──────────────────────────────────────────────────────


async def run_autofix_flow(
    *,
    fingerprint: str,
    file_target: str | None,
    exc_type: str,
    error_message: str,
    redacted_traceback: str,
    func_name: str = "unknown",
    line: int = 0,
    repo_root: Path | None = None,
) -> AutoFixOutcome:
    """The full dev-mode pipeline. Never raises into the caller."""
    repo_root = repo_root or Path.cwd()
    fp12 = fingerprint[:12]

    ok, reason = preflight(
        fingerprint=fingerprint, file_target=file_target, repo_root=repo_root
    )
    if not ok:
        _update_attempt(
            fingerprint=fingerprint,
            status="rejected",
            branch=None,
            test_path=None,
            file_target=file_target,
            diff=None,
            error_message=f"{reason}: {error_message}",
            exc_type=exc_type,
        )
        return AutoFixOutcome(status="rejected", reason=reason)

    # Single-in-flight mutex — if another auto-fix is running, decline.
    with _locks.try_acquire() as acquired:
        if not acquired:
            _update_attempt(
                fingerprint=fingerprint,
                status="rejected",
                branch=None,
                test_path=None,
                file_target=file_target,
                diff=None,
                error_message="another auto-fix already in flight",
                exc_type=exc_type,
            )
            return AutoFixOutcome(
                status="rejected", reason="another auto-fix already in flight"
            )

        # Capture the branch we started on so we can return to it on failure.
        original_branch = _git.current_branch(repo_root) or "HEAD"
        branch = sandbox_branch(repo_root, fp12)
        if branch is None:
            _update_attempt(
                fingerprint=fingerprint,
                status="rejected",
                branch=None,
                test_path=None,
                file_target=file_target,
                diff=None,
                error_message="sandbox branch creation failed",
                exc_type=exc_type,
            )
            return AutoFixOutcome(status="rejected", reason="sandbox creation failed")

        settings = _config.get_settings()
        from packages.error_responder import sdk_client as _sdk

        test_path_expected = f"tests/regression/auto_fix/test_{fp12}.py"
        prompt = _sdk.render_prompt(
            repo_root=repo_root,
            file_path=file_target or "",
            line=line,
            func=func_name,
            exc_type=exc_type,
            error_message=error_message,
            traceback=redacted_traceback,
            fp12=fp12,
            denylist=settings.autofix_denylist,
        )

        try:
            proposal = await _sdk.run_sdk_proposal(
                prompt=prompt,
                cwd=repo_root,
                max_turns=settings.sdk_max_turns,
                timeout_seconds=settings.sdk_timeout_seconds,
                test_path_expected=test_path_expected,
            )
        except Exception as e:
            log.warning("SDK invocation failed: %s", e)
            revert_sandbox(repo_root, branch, original_branch)
            _update_attempt(
                fingerprint=fingerprint,
                status="failed",
                branch=branch,
                test_path=None,
                file_target=file_target,
                diff=None,
                error_message=f"SDK failure: {type(e).__name__}: {e}",
                exc_type=exc_type,
            )
            return AutoFixOutcome(
                status="failed",
                reason=f"SDK invocation: {e}",
                branch=branch,
            )

        diff_snapshot = capture_diff(repo_root)

        outcome = verify(
            repo_root,
            test_path=proposal.test_path or test_path_expected,
            source_files=proposal.source_files,
        )
        if not outcome.ok:
            log.info("verification failed: %s", outcome.reason)
            revert_sandbox(repo_root, branch, original_branch)
            _update_attempt(
                fingerprint=fingerprint,
                status="failed",
                branch=branch,
                test_path=proposal.test_path,
                file_target=file_target,
                diff=diff_snapshot,
                error_message=outcome.reason,
                exc_type=exc_type,
            )
            return AutoFixOutcome(
                status="failed",
                reason=outcome.reason,
                branch=branch,
                test_path=proposal.test_path,
                source_files=proposal.source_files,
                diff=diff_snapshot,
            )

        committed = git_commit_fix(
            repo_root, fp12=fp12, exc_type=exc_type, file_target=file_target or "(?)"
        )
        if not committed:
            revert_sandbox(repo_root, branch, original_branch)
            _update_attempt(
                fingerprint=fingerprint,
                status="failed",
                branch=branch,
                test_path=proposal.test_path,
                file_target=file_target,
                diff=diff_snapshot,
                error_message="git commit failed",
                exc_type=exc_type,
            )
            return AutoFixOutcome(
                status="failed",
                reason="git commit failed",
                branch=branch,
                source_files=proposal.source_files,
                diff=diff_snapshot,
            )

        # Commit landed on the sandbox branch — branch on the deploy mode.
        # main is intentionally NEVER touched: uvicorn --reload picks up the
        # source-file change via watchfiles; workers emit the canonical
        # autofix.restart_required signal so an operator can restart them.
        pr_url: str | None = None
        if settings.autofix_deploy == "pr":
            pr_url, deploy_reason = _deploy_via_pr(
                repo_root=repo_root,
                branch=branch,
                base_branch=original_branch,
                settings=settings,
                fp12=fp12,
                fingerprint=fingerprint,
                exc_type=exc_type,
                error_message=error_message,
                file_target=file_target,
                diff_snapshot=diff_snapshot,
                test_path=proposal.test_path,
            )
            if pr_url is None:
                _update_attempt(
                    fingerprint=fingerprint,
                    status="failed",
                    branch=branch,
                    test_path=proposal.test_path,
                    file_target=file_target,
                    diff=diff_snapshot,
                    error_message=deploy_reason,
                    exc_type=exc_type,
                )
                return AutoFixOutcome(
                    status="failed",
                    reason=deploy_reason,
                    branch=branch,
                    source_files=proposal.source_files,
                    diff=diff_snapshot,
                )

        log.info(
            "autofix.restart_required source=worker branch=%s file=%s fp=%s pr=%s",
            branch,
            file_target,
            fp12,
            pr_url or "—",
        )
        _update_attempt(
            fingerprint=fingerprint,
            status="deployed",
            branch=branch,
            test_path=proposal.test_path,
            file_target=file_target,
            diff=diff_snapshot,
            error_message=error_message,
            exc_type=exc_type,
            pr_url=pr_url,
        )

        return AutoFixOutcome(
            status="deployed",
            branch=branch,
            test_path=proposal.test_path,
            source_files=proposal.source_files,
            diff=diff_snapshot,
            pr_url=pr_url,
        )


def _deploy_via_pr(
    *,
    repo_root: Path,
    branch: str,
    base_branch: str,
    settings,
    fp12: str,
    fingerprint: str,
    exc_type: str,
    error_message: str,
    file_target: str | None,
    diff_snapshot: str | None,
    test_path: str | None,
) -> tuple[str | None, str]:
    """Push the sandbox branch and open a PR. Returns ``(pr_url, reason)``.

    On success, ``pr_url`` is set and ``reason`` is empty. On failure,
    ``pr_url`` is None and ``reason`` carries the failure cause for the
    AutoFixAttempt row.
    """
    if not settings.github_token or not settings.github_repo:
        return None, "AUTOFIX_DEPLOY=pr but GITHUB_TOKEN/GITHUB_REPO not set"

    pushed_ok, push_err = _git.push_branch(branch, cwd=repo_root)
    if not pushed_ok:
        return None, f"git push failed: {push_err}"

    from packages.error_responder import github_pr as _gh_pr

    body = _gh_pr.render_pr_body(
        fingerprint=fingerprint,
        exc_type=exc_type,
        error_message=error_message,
        file_target=file_target,
        correlation_ids={},  # populated by dispatcher in PR-A; not threaded here
        redacted_traceback="",  # see above — keeps the PR body lean
        test_path=test_path,
        diff_excerpt=diff_snapshot,
    )
    title = f"[auto-fix] {exc_type} at {file_target or '(unknown)'}"
    outcome = _gh_pr.open_pull_request(
        repo=settings.github_repo,
        token=settings.github_token,
        head_branch=branch,
        base_branch=base_branch,
        title=title[:160],
        body=body,
        fingerprint=fingerprint,
    )
    if outcome.action in ("opened", "exists") and outcome.url:
        return outcome.url, ""
    return None, f"PR API failed: {outcome.error or outcome.action}"


def run_autofix_flow_sync(**kwargs) -> AutoFixOutcome:
    """Sync entry point — workers call this from their main-loop wrapper."""
    try:
        return asyncio.run(run_autofix_flow(**kwargs))
    except Exception as e:
        log.error("run_autofix_flow_sync swallowed an error: %s", e)
        return AutoFixOutcome(status="failed", reason=str(e))


# Hint for callers; not used by the orchestrator itself.
PYTHON_EXECUTABLE = sys.executable
PROJECT_ROOT_HINT = os.environ.get("SLM_FORGE_PROJECT_ROOT", str(Path.cwd()))
