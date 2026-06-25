"""PR-B² — dev-mode ``AUTOFIX_DEPLOY=pr`` path.

The legacy ``auto-commit-reload`` deploy mode (tested in ``test_autofix.py``)
commits to a local sandbox branch and stops. The ``pr`` mode pushes that
branch to ``origin`` and opens a GitHub PR. Tests here:

  - ``_git.push_branch`` shells ``git push -u origin <branch>`` and returns a
    structured ``(ok, error)`` tuple.
  - ``github_pr.open_pull_request`` posts to ``POST /repos/{repo}/pulls``,
    falls back to existing-PR detection on 422 (or pre-checks via
    ``GET /repos/{repo}/pulls?head=...``).
  - ``run_autofix_flow`` with ``AUTOFIX_DEPLOY=pr`` calls both helpers in
    order, persists ``pr_url`` on the ``AutoFixAttempt`` row, and reports
    ``status='deployed'``.
  - Push failure → status ``failed``, PR not called.
  - PR-API failure → status ``failed`` with push already done.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.autofix import AutoFixAttempt
from apps.api.services import db as db_module
from packages.error_responder import _git, autofix, sdk_client
from packages.error_responder import config as _config

# ── Shared fixtures (mirror test_autofix.py + test_reporter.py) ─────────


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, check=False, capture_output=True, text=True, cwd=str(cwd), timeout=10
    )


def _init_repo(root: Path, *, on_branch: str = "feature/x") -> None:
    _run(["git", "init", "-q", "-b", "main"], cwd=root)
    _run(["git", "config", "user.email", "t@t"], cwd=root)
    _run(["git", "config", "user.name", "t"], cwd=root)
    (root / "README.md").write_text("seed\n")
    _run(["git", "add", "."], cwd=root)
    _run(["git", "commit", "-q", "-m", "init"], cwd=root)
    # Fake a remote so `git push` has somewhere to point at — used only as a
    # validation handle; the real subprocess is monkey-patched out in tests.
    _run(["git", "remote", "add", "origin", "git@github.com:owner/repo.git"], cwd=root)
    if on_branch != "main":
        _run(["git", "checkout", "-q", "-b", on_branch], cwd=root)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'autofix.db'}")
    SQLModel.metadata.create_all(eng, tables=[AutoFixAttempt.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


@pytest.fixture(autouse=True)
def _pr_mode_env(monkeypatch: pytest.MonkeyPatch):
    """Everything green by default; individual tests override what they need."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "development")
    monkeypatch.setenv("AUTOFIX_ENABLED", "true")
    monkeypatch.setenv("AUTOFIX_DEPLOY", "pr")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "stub")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    _config.reset_settings_cache()
    yield
    _config.reset_settings_cache()


def _seed_buggy_project(repo: Path) -> tuple[str, str]:
    """Same as test_autofix.py — a tiny module the mocked SDK can 'fix'."""
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


async def _fake_sdk_real_fix(repo: Path, src_path: str, test_path: str):
    """Mocked SDK that applies a real fix + writes a real passing test."""
    async def _impl(*, prompt, cwd, max_turns, timeout_seconds, test_path_expected):
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

    return _impl


# ── push_branch ─────────────────────────────────────────────────────────


def test_push_branch_invokes_git_push_with_upstream(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_git.subprocess, "run", fake_run)

    ok, error = _git.push_branch("auto-fix/abc-1", cwd=repo)

    assert ok is True
    assert error is None
    assert len(calls) == 1
    assert calls[0][:5] == ["git", "push", "-u", "origin", "auto-fix/abc-1"]


def test_push_branch_returns_error_on_nonzero(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="fatal: repository 'origin' not found\n",
        )

    monkeypatch.setattr(_git.subprocess, "run", fake_run)

    ok, error = _git.push_branch("auto-fix/abc-1", cwd=repo)

    assert ok is False
    assert error is not None
    assert "fatal" in error


# ── github_pr.open_pull_request ─────────────────────────────────────────


class _MockResponse:
    def __init__(self, status: int, payload: Any):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=self  # type: ignore[arg-type]
            )


class _MockPRClient:
    """Mimics the slice of httpx.Client that github_pr.py uses."""

    def __init__(
        self,
        *,
        existing_pr: bool = False,
        fail_post: bool = False,
        post_status: int = 201,
    ):
        self.existing_pr = existing_pr
        self.fail_post = fail_post
        self.post_status = post_status
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(self, url, *, params=None, **_):
        self.get_calls.append({"url": url, "params": params})
        if self.existing_pr:
            return _MockResponse(
                200,
                [{"number": 7, "html_url": "https://github.com/owner/repo/pull/7"}],
            )
        return _MockResponse(200, [])

    def post(self, url, *, headers=None, json=None, **_):
        self.post_calls.append({"url": url, "json": json})
        if self.fail_post:
            return _MockResponse(self.post_status, {"message": "boom"})
        return _MockResponse(
            201,
            {"number": 99, "html_url": "https://github.com/owner/repo/pull/99"},
        )

    def close(self):
        pass


def test_open_pull_request_creates_new_pr_when_none_exists():
    from packages.error_responder import github_pr

    client = _MockPRClient(existing_pr=False)
    outcome = github_pr.open_pull_request(
        repo="owner/repo",
        token="ghp_x",
        head_branch="auto-fix/abc-1",
        base_branch="main",
        title="[auto-fix] ValueError in lib.py",
        body="redacted body",
        fingerprint="abcdef" * 10 + "abcd",
        client=client,
    )

    assert outcome.action == "opened"
    assert outcome.url == "https://github.com/owner/repo/pull/99"
    assert len(client.post_calls) == 1
    body = client.post_calls[0]["json"]
    assert body["head"] == "auto-fix/abc-1"
    assert body["base"] == "main"
    assert body["title"].startswith("[auto-fix]")


def test_open_pull_request_returns_existing_pr_if_one_is_open():
    from packages.error_responder import github_pr

    client = _MockPRClient(existing_pr=True)
    outcome = github_pr.open_pull_request(
        repo="owner/repo",
        token="ghp_x",
        head_branch="auto-fix/abc-1",
        base_branch="main",
        title="ignored",
        body="ignored",
        fingerprint="x" * 64,
        client=client,
    )

    assert outcome.action == "exists"
    assert outcome.url == "https://github.com/owner/repo/pull/7"
    assert len(client.post_calls) == 0, "must not POST when an open PR already exists"


def test_open_pull_request_returns_error_on_5xx():
    from packages.error_responder import github_pr

    client = _MockPRClient(fail_post=True, post_status=500)
    outcome = github_pr.open_pull_request(
        repo="owner/repo",
        token="ghp_x",
        head_branch="auto-fix/abc-1",
        base_branch="main",
        title="t",
        body="b",
        fingerprint="x" * 64,
        client=client,
    )

    assert outcome.action == "error"
    assert outcome.url is None
    assert outcome.error is not None


# ── Full pipeline: AUTOFIX_DEPLOY=pr ────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_pr_mode_pushes_and_opens_pr(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    isolated_db,
):
    src_path, test_path = _seed_buggy_project(repo)

    fake_sdk = await _fake_sdk_real_fix(repo, src_path, test_path)
    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    pushed: list[str] = []

    def fake_push(branch: str, *, cwd=None):
        pushed.append(branch)
        return True, None

    monkeypatch.setattr(_git, "push_branch", fake_push)
    # Also patch the reference held inside autofix.py — it imports the module.
    monkeypatch.setattr(autofix._git, "push_branch", fake_push)

    from packages.error_responder import github_pr

    pr_calls: list[dict[str, Any]] = []

    def fake_open_pr(**kw):
        pr_calls.append(kw)
        return github_pr.PROutcome(
            action="opened",
            url="https://github.com/owner/repo/pull/123",
        )

    monkeypatch.setattr(github_pr, "open_pull_request", fake_open_pr)

    main_before = _run(["git", "rev-parse", "main"], cwd=repo).stdout.strip()

    outcome = await autofix.run_autofix_flow(
        fingerprint="0123456789abdeadbeef",
        file_target=src_path,
        exc_type="AssertionError",
        error_message="add(2,3) returned -1",
        redacted_traceback="(stub)",
        repo_root=repo,
    )

    assert outcome.status == "deployed", outcome.reason
    assert outcome.pr_url == "https://github.com/owner/repo/pull/123"

    # main MUST NOT have moved.
    main_after = _run(["git", "rev-parse", "main"], cwd=repo).stdout.strip()
    assert main_before == main_after

    # Sandbox branch was created + pushed + PR opened — in that order, once.
    assert len(pushed) == 1
    assert pushed[0].startswith("auto-fix/0123456789ab-")
    assert len(pr_calls) == 1
    assert pr_calls[0]["head_branch"] == pushed[0]
    assert pr_calls[0]["base_branch"] == "feature/x"  # the branch we started on

    # AutoFixAttempt row carries pr_url.
    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "deployed"
        assert row.pr_url == "https://github.com/owner/repo/pull/123"
        assert row.branch is not None and row.branch.startswith("auto-fix/")


@pytest.mark.asyncio
async def test_pipeline_pr_mode_marks_failed_on_push_error(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    isolated_db,
):
    src_path, test_path = _seed_buggy_project(repo)
    fake_sdk = await _fake_sdk_real_fix(repo, src_path, test_path)
    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    def fake_push(branch: str, *, cwd=None):
        return False, "fatal: could not read from origin"

    monkeypatch.setattr(_git, "push_branch", fake_push)
    monkeypatch.setattr(autofix._git, "push_branch", fake_push)

    from packages.error_responder import github_pr

    pr_calls: list[dict[str, Any]] = []

    def fake_open_pr(**kw):
        pr_calls.append(kw)
        return github_pr.PROutcome(action="opened", url="…")

    monkeypatch.setattr(github_pr, "open_pull_request", fake_open_pr)

    outcome = await autofix.run_autofix_flow(
        fingerprint="ffffffff" * 8,
        file_target=src_path,
        exc_type="AssertionError",
        error_message="bug",
        redacted_traceback="(stub)",
        repo_root=repo,
    )

    assert outcome.status == "failed"
    assert "push" in (outcome.reason or "").lower()
    assert pr_calls == [], "PR API must NOT be called when push fails"

    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].pr_url is None


@pytest.mark.asyncio
async def test_pipeline_pr_mode_marks_failed_on_pr_api_error(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    isolated_db,
):
    src_path, test_path = _seed_buggy_project(repo)
    fake_sdk = await _fake_sdk_real_fix(repo, src_path, test_path)
    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    pushed: list[str] = []

    def fake_push(branch: str, *, cwd=None):
        pushed.append(branch)
        return True, None

    monkeypatch.setattr(_git, "push_branch", fake_push)
    monkeypatch.setattr(autofix._git, "push_branch", fake_push)

    from packages.error_responder import github_pr

    def fake_open_pr(**kw):
        return github_pr.PROutcome(action="error", url=None, error="github 500")

    monkeypatch.setattr(github_pr, "open_pull_request", fake_open_pr)

    outcome = await autofix.run_autofix_flow(
        fingerprint="eeeeeeee" * 8,
        file_target=src_path,
        exc_type="AssertionError",
        error_message="bug",
        redacted_traceback="(stub)",
        repo_root=repo,
    )

    assert outcome.status == "failed"
    assert "PR" in (outcome.reason or "")
    assert len(pushed) == 1, "push WAS attempted before the PR API was called"

    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].pr_url is None


@pytest.mark.asyncio
async def test_pipeline_auto_commit_reload_mode_skips_push_and_pr(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    isolated_db,
):
    """Regression: legacy ``auto-commit-reload`` mode must NOT call push or PR."""
    monkeypatch.setenv("AUTOFIX_DEPLOY", "auto-commit-reload")
    _config.reset_settings_cache()

    src_path, test_path = _seed_buggy_project(repo)
    fake_sdk = await _fake_sdk_real_fix(repo, src_path, test_path)
    monkeypatch.setattr(sdk_client, "run_sdk_proposal", fake_sdk)

    def must_not_push(*a, **kw):
        raise AssertionError("push_branch must not be called in auto-commit-reload mode")

    monkeypatch.setattr(_git, "push_branch", must_not_push)
    monkeypatch.setattr(autofix._git, "push_branch", must_not_push)

    from packages.error_responder import github_pr

    def must_not_pr(**kw):
        raise AssertionError("open_pull_request must not be called in auto-commit-reload mode")

    monkeypatch.setattr(github_pr, "open_pull_request", must_not_pr)

    outcome = await autofix.run_autofix_flow(
        fingerprint="cccccccc" * 8,
        file_target=src_path,
        exc_type="AssertionError",
        error_message="bug",
        redacted_traceback="(stub)",
        repo_root=repo,
    )

    assert outcome.status == "deployed"
    assert outcome.pr_url is None

    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1
        assert rows[0].status == "deployed"
        assert rows[0].pr_url is None
