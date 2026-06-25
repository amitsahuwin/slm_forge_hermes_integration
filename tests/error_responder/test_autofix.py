"""PR-B — autofix orchestrator: preflight gates + sandbox + quality gate.

Every test ISOLATES the orchestrator from the real repo by running inside
a fresh ``git init`` worktree (``tmp_path``). The SDK call itself is
monkey-patched so we don't need a live Anthropic endpoint.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from packages.error_responder import autofix, sdk_client
from packages.error_responder import config as _config


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, check=False, capture_output=True, text=True, cwd=str(cwd), timeout=10
    )


def _init_repo(root: Path, *, on_branch: str = "feature/x") -> None:
    """Bootstrap a tiny git repo in ``root`` with one commit on ``on_branch``."""
    _run(["git", "init", "-q", "-b", "main"], cwd=root)
    _run(["git", "config", "user.email", "t@t"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "README.md").write_text("seed\n")
    _run(["git", "add", "."], cwd=root)
    _run(["git", "commit", "-q", "-m", "init"], cwd=root)
    if on_branch != "main":
        _run(["git", "checkout", "-q", "-b", on_branch], cwd=root)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    # Make every helper that takes ``repo_root`` see this dir.
    monkeypatch.chdir(root)
    return root


@pytest.fixture(autouse=True)
def _autofix_env(monkeypatch: pytest.MonkeyPatch):
    """Default to "everything green" — individual tests flip what they need.

    ``AUTOFIX_DEPLOY`` is pinned to the legacy default so the test_autofix_pr
    suite (which sets it to ``pr``) can't leak into these tests via the
    ambient process env (e.g. when ``.env`` exports ``AUTOFIX_DEPLOY=pr``).
    """
    monkeypatch.setenv("DEPLOYMENT_MODE", "development")
    monkeypatch.setenv("AUTOFIX_ENABLED", "true")
    monkeypatch.setenv("AUTOFIX_DEPLOY", "auto-commit-reload")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stub")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    _config.reset_settings_cache()
    yield
    _config.reset_settings_cache()


# ── Preflight gates ────────────────────────────────────────────────────


def test_preflight_passes_with_clean_repo_and_valid_target(repo: Path):
    (repo / "src.py").write_text("x = 1\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "src"], cwd=repo)

    ok, reason = autofix.preflight(
        fingerprint="abc123", file_target="src.py", repo_root=repo
    )
    assert ok, reason


def test_preflight_refuses_when_disabled(monkeypatch: pytest.MonkeyPatch, repo: Path):
    monkeypatch.setenv("AUTOFIX_ENABLED", "false")
    _config.reset_settings_cache()
    ok, reason = autofix.preflight(
        fingerprint="abc", file_target="src.py", repo_root=repo
    )
    assert not ok
    assert "AUTOFIX_ENABLED" in reason


def test_preflight_refuses_on_main(repo: Path):
    _run(["git", "checkout", "-q", "main"], cwd=repo)
    ok, reason = autofix.preflight(
        fingerprint="abc", file_target="src.py", repo_root=repo
    )
    assert not ok
    assert "main" in reason


def test_preflight_refuses_dirty_tree(repo: Path):
    (repo / "dirty.py").write_text("unstaged\n")
    ok, reason = autofix.preflight(
        fingerprint="abc", file_target="src.py", repo_root=repo
    )
    assert not ok
    assert "dirty" in reason.lower()


def test_preflight_refuses_target_in_tests_dir(repo: Path):
    ok, reason = autofix.preflight(
        fingerprint="abc",
        file_target="tests/api/test_x.py",
        repo_root=repo,
    )
    assert not ok
    assert "tests/" in reason


def test_preflight_refuses_target_in_denylist(repo: Path):
    ok, reason = autofix.preflight(
        fingerprint="abc",
        file_target="apps/api/main.py",  # in default denylist
        repo_root=repo,
    )
    assert not ok
    assert "DENYLIST" in reason


def test_preflight_refuses_file_with_no_autofix_marker(repo: Path):
    src = repo / "guarded.py"
    src.write_text("# NO_AUTOFIX — leave this alone\nx = 1\n")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "g"], cwd=repo)

    ok, reason = autofix.preflight(
        fingerprint="abc", file_target="guarded.py", repo_root=repo
    )
    assert not ok
    assert "NO_AUTOFIX" in reason


def test_preflight_refuses_when_no_target_frame(repo: Path):
    ok, reason = autofix.preflight(
        fingerprint="abc", file_target=None, repo_root=repo
    )
    assert not ok
    assert "no project frame" in reason


def test_preflight_refuses_when_sdk_unavailable(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    monkeypatch.setattr(sdk_client, "sdk_available", lambda: False)
    ok, reason = autofix.preflight(
        fingerprint="abc", file_target="src.py", repo_root=repo
    )
    assert not ok
    assert "claude_agent_sdk" in reason


# Missing-creds + AUTOFIX_ENABLED=true is handled by the config layer
# (get_settings raises a RuntimeError at startup). See
# ``test_config.test_autofix_enabled_without_anthropic_creds_raises``.


# ── Sandbox helpers ────────────────────────────────────────────────────


def test_sandbox_branch_creates_and_checks_out(repo: Path):
    branch = autofix.sandbox_branch(repo, fp12="deadbeef1234")
    assert branch is not None
    assert branch.startswith("auto-fix/deadbeef1234-")
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    assert current == branch


def test_revert_sandbox_returns_to_original_branch(repo: Path):
    branch = autofix.sandbox_branch(repo, fp12="fp")
    assert branch is not None
    autofix.revert_sandbox(repo, branch, "feature/x")
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    assert current == "feature/x"
    listing = _run(["git", "branch", "--list", branch], cwd=repo).stdout
    assert branch not in listing  # the sandbox branch was deleted


# ── Quality gate ───────────────────────────────────────────────────────


def _seed_buggy_project(repo: Path) -> tuple[str, str]:
    """Create a small Python module that throws, plus a fixed version on a
    sibling branch. Returns the source path + test path the fix uses."""
    src_path = "lib.py"
    (repo / src_path).write_text("def add(a, b):\n    return a - b  # bug\n")
    (repo / "tests").mkdir()
    (repo / "tests/__init__.py").write_text("")
    (repo / "tests/regression").mkdir()
    (repo / "tests/regression/__init__.py").write_text("")
    (repo / "tests/regression/auto_fix").mkdir()
    (repo / "tests/regression/auto_fix/__init__.py").write_text("")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-q", "-m", "seed bug"], cwd=repo)
    return src_path, "tests/regression/auto_fix/test_demo.py"


def test_quality_gate_rejects_tautological_test(repo: Path):
    """A test that passes against the pre-fix code MUST be rejected."""
    src_path, test_path = _seed_buggy_project(repo)

    # Apply a "fix" (returns a+b now) + a "test" that always passes regardless.
    (repo / src_path).write_text("def add(a, b):\n    return a + b\n")
    (repo / test_path).write_text(
        textwrap.dedent(
            """
            def test_tautology():
                assert 1 == 1  # passes with OR without the fix
            """
        ).strip()
        + "\n"
    )

    ok, reason = autofix.quality_gate(
        repo, test_path=test_path, source_files=[src_path]
    )
    assert not ok
    assert "tautological" in reason


def test_quality_gate_accepts_real_fix(repo: Path):
    """A test that FAILS without the fix and PASSES with it is accepted."""
    src_path, test_path = _seed_buggy_project(repo)

    (repo / src_path).write_text("def add(a, b):\n    return a + b\n")
    (repo / test_path).write_text(
        textwrap.dedent(
            """
            from lib import add

            def test_add_is_sum():
                assert add(2, 3) == 5
            """
        ).strip()
        + "\n"
    )

    ok, reason = autofix.quality_gate(
        repo, test_path=test_path, source_files=[src_path]
    )
    assert ok, f"expected accept; reason={reason!r}"


# ── Full pipeline (SDK mocked) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_deploys_real_fix(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """End-to-end: mocked SDK applies the fix + writes a passing test, the
    orchestrator runs the quality gate + full pytest + commits."""
    src_path, test_path = _seed_buggy_project(repo)

    async def fake_sdk(*, prompt, cwd, max_turns, timeout_seconds, test_path_expected):
        # The SDK normally Edit-s the source file + writes the test in-place.
        (cwd / src_path).write_text("def add(a, b):\n    return a + b\n")
        (cwd / test_path).write_text(
            textwrap.dedent(
                """
                from lib import add

                def test_add_is_sum():
                    assert add(2, 3) == 5
                """
            ).strip()
            + "\n"
        )
        return sdk_client.AutoFixProposal(
            source_files=[src_path],
            test_path=test_path,
            test_content=(cwd / test_path).read_text(),
            raw_response="(mock)",
        )

    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    # Snapshot main BEFORE the orchestrator runs so we can assert it didn't move.
    main_before = _run(["git", "rev-parse", "main"], cwd=repo).stdout.strip()

    outcome = await autofix.run_autofix_flow(
        fingerprint="0123456789abdeadbeef",
        file_target=src_path,
        exc_type="AssertionError",
        error_message="add(2,3) returned -1",
        redacted_traceback="(stub traceback)",
        repo_root=repo,
    )

    assert outcome.status == "deployed", outcome.reason
    # main HEAD must NOT have moved during the auto-fix.
    main_after = _run(["git", "rev-parse", "main"], cwd=repo).stdout.strip()
    assert main_before == main_after, "auto-fix must not touch main"
    # The branch carries the commit.
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    assert current.startswith("auto-fix/")
    log = _run(["git", "log", "-1", "--pretty=%s"], cwd=repo).stdout
    assert "auto-fix(0123456789ab)" in log


@pytest.mark.asyncio
async def test_pipeline_rejects_on_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    (repo / "leftover.py").write_text("noise\n")
    called = {"sdk": 0}

    async def must_not_call(**_):
        called["sdk"] += 1
        raise AssertionError("SDK must not be invoked on dirty tree")

    monkeypatch.setattr(sdk_client, "run_sdk_proposal", must_not_call)

    outcome = await autofix.run_autofix_flow(
        fingerprint="aabbccddeeff",
        file_target="src.py",
        exc_type="ValueError",
        error_message="boom",
        redacted_traceback="…",
        repo_root=repo,
    )

    assert outcome.status == "rejected"
    assert called["sdk"] == 0


@pytest.mark.asyncio
async def test_pipeline_fails_loudly_when_sdk_raises(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    src_path, _ = _seed_buggy_project(repo)

    async def boom(**_):
        raise RuntimeError("anthropic API 5xx")

    monkeypatch.setattr(sdk_client, "run_sdk_proposal", boom)

    outcome = await autofix.run_autofix_flow(
        fingerprint="ffeeddccbbaa",
        file_target=src_path,
        exc_type="ValueError",
        error_message="boom",
        redacted_traceback="…",
        repo_root=repo,
    )

    assert outcome.status == "failed"
    assert "SDK" in outcome.reason
    # Workspace must be restored.
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    assert current == "feature/x"


@pytest.mark.asyncio
async def test_pipeline_handles_tautological_fix(
    monkeypatch: pytest.MonkeyPatch, repo: Path
):
    """When the SDK proposes a vacuous test, the quality gate rejects it
    and the pipeline ends as ``failed`` (NOT ``deployed``)."""
    src_path, test_path = _seed_buggy_project(repo)

    async def fake_sdk(**kwargs):
        cwd = kwargs["cwd"]
        # Real fix...
        (cwd / src_path).write_text("def add(a, b):\n    return a + b\n")
        # ...but a tautological test that passes regardless.
        (cwd / test_path).write_text("def test_tautology():\n    assert 1\n")
        return sdk_client.AutoFixProposal(
            source_files=[src_path],
            test_path=test_path,
            test_content="(mock)",
            raw_response="(mock)",
        )

    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    outcome = await autofix.run_autofix_flow(
        fingerprint="ddeeaabbccff",
        file_target=src_path,
        exc_type="AssertionError",
        error_message="add(2,3)=-1",
        redacted_traceback="…",
        repo_root=repo,
    )

    assert outcome.status == "failed"
    assert "tautological" in outcome.reason
