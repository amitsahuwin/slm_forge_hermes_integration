"""PR-A — reporter + GitHub-issue path.

Mocks both the GitHub HTTP surface (via a fake ``httpx.Client``) and the
``apps.api.models.autofix`` persistence layer with a per-test SQLite
engine. Asserts:

  - Production-mode capture writes an ``AutoFixAttempt`` row with the
    correct fingerprint, mode, source, redaction.
  - First occurrence opens a new GitHub issue; second occurrence with the
    same fingerprint comments on it (no second open).
  - Storm-protection batches: once the sliding-window count crosses
    ``storm_threshold`` the dispatch is suppressed (no HTTP call), but
    the AutoFixAttempt row still records the occurrence.
  - The reporter NEVER raises into the caller — even when GitHub 5xxs.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.autofix import AutoFixAttempt
from apps.api.services import db as db_module
from packages.error_responder import config as _config
from packages.error_responder import github_issue as gh
from packages.error_responder import reporter


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'autofix.db'}")
    SQLModel.metadata.create_all(eng, tables=[AutoFixAttempt.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


# Capture the REAL function before any test patches the module — otherwise
# the lambda below would recurse through whatever the previous test bound.
_REAL_OPEN_OR_COMMENT = gh.open_or_comment_issue


def _patch_gh(monkeypatch: pytest.MonkeyPatch, client) -> None:
    """Inject a mock httpx-shaped ``client`` into both modules' bound
    references so reporter.py picks up the patch regardless of import path.
    """
    def _shim(**kw):
        # Reporter passes ``client=None`` explicitly; override with the mock
        # so we hit the recorder instead of real httpx.
        kw["client"] = client
        return _REAL_OPEN_OR_COMMENT(**kw)

    monkeypatch.setattr(gh, "open_or_comment_issue", _shim)
    monkeypatch.setattr(reporter._gh, "open_or_comment_issue", _shim)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch):
    """Reset module-level state + force production-mode settings."""
    reporter.reset_storm_state()
    _config.reset_settings_cache()
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    monkeypatch.setenv("ERROR_REPORTER_STORM_THRESHOLD", "3")
    yield
    reporter.reset_storm_state()
    _config.reset_settings_cache()


class _MockGitHubClient:
    """Mimics the slice of ``httpx.Client`` ``github_issue`` actually uses.

    Records every call so tests can assert exactly what happened.
    """

    def __init__(self, *, existing_match: bool = False, fail_open: bool = False):
        self.existing_match = existing_match
        self.fail_open = fail_open
        self.search_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.comment_calls: list[dict[str, Any]] = []
        self._next_issue = 100

    # ─── search/issues ─────────────────────────────────────────────
    def get(self, url, *, params=None, **_):
        self.search_calls.append({"url": url, "params": params})
        if self.existing_match:
            payload = {
                "items": [
                    {"number": 42, "html_url": "https://github.com/owner/repo/issues/42"}
                ]
            }
        else:
            payload = {"items": []}
        return _MockResponse(200, payload)

    # ─── POST /issues or /issues/{n}/comments ──────────────────────
    def post(self, url, *, headers=None, json=None, **_):
        if url.endswith("/issues"):
            self.post_calls.append({"url": url, "json": json})
            if self.fail_open:
                return _MockResponse(500, {"message": "boom"})
            n = self._next_issue
            self._next_issue += 1
            return _MockResponse(
                201,
                {"number": n, "html_url": f"https://github.com/owner/repo/issues/{n}"},
            )
        if "/comments" in url:
            self.comment_calls.append({"url": url, "json": json})
            return _MockResponse(201, {})
        raise AssertionError(f"unexpected POST {url}")

    def close(self):
        pass


class _MockResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req, response=self  # type: ignore[arg-type]
            )


def _raise_known(msg: str = "test boom") -> BaseException:
    """Helper — produces an exception with a stable fingerprint."""
    try:
        raise ValueError(msg)
    except BaseException as e:
        return e


# ── Tests ────────────────────────────────────────────────────────────


def test_first_occurrence_opens_issue(monkeypatch: pytest.MonkeyPatch, isolated_db):
    client = _MockGitHubClient(existing_match=False)
    _patch_gh(monkeypatch, client)

    exc = _raise_known()
    reporter.report_exception_sync(exc, source="api")

    assert len(client.post_calls) == 1, "first occurrence should POST a new issue"
    assert len(client.comment_calls) == 0
    posted = client.post_calls[0]["json"]
    assert "[auto]" in posted["title"]
    assert "ValueError" in posted["title"]
    # The body must carry the fingerprint anchor so dedupe search works.
    assert "sha256:" in posted["body"]

    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.error_type == "ValueError"
        assert row.mode == "production"
        assert row.status == "reported"
        assert row.issue_url is not None


def test_second_occurrence_comments_on_existing(
    monkeypatch: pytest.MonkeyPatch, isolated_db
):
    client = _MockGitHubClient(existing_match=True)
    _patch_gh(monkeypatch, client)

    exc = _raise_known()
    reporter.report_exception_sync(exc, source="api")

    # No new issue opened; one comment posted to the existing issue.
    assert len(client.post_calls) == 0
    assert len(client.comment_calls) == 1
    assert "/issues/42/comments" in client.comment_calls[0]["url"]


def test_storm_protection_suppresses_dispatch_above_threshold(
    monkeypatch: pytest.MonkeyPatch, isolated_db
):
    """ERROR_REPORTER_STORM_THRESHOLD=3 → first 3 occurrences dispatch; 4+ suppress."""
    client = _MockGitHubClient(existing_match=False)
    _patch_gh(monkeypatch, client)

    for _ in range(5):
        reporter.report_exception_sync(_raise_known(), source="api")

    # Only the first 3 hit GitHub.
    assert len(client.post_calls) + len(client.comment_calls) <= 3
    # But all 5 occurrences are persisted (the last two as ``rejected``).
    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 5
        rejected = [r for r in rows if r.status == "rejected"]
        assert len(rejected) == 2


def test_reporter_swallows_github_500(monkeypatch: pytest.MonkeyPatch, isolated_db):
    """A 500 from GitHub must NOT propagate into the caller."""
    client = _MockGitHubClient(fail_open=True)
    _patch_gh(monkeypatch, client)

    # MUST NOT raise.
    reporter.report_exception_sync(_raise_known(), source="api")

    # Row is still persisted (issue_url is None because the open failed).
    with Session(isolated_db) as s:
        rows = s.exec(select(AutoFixAttempt)).all()
        assert len(rows) == 1


def test_disabled_via_env_short_circuits(
    monkeypatch: pytest.MonkeyPatch, isolated_db
):
    monkeypatch.setenv("ERROR_REPORTER_ENABLED", "false")
    _config.reset_settings_cache()

    client = _MockGitHubClient()
    _patch_gh(monkeypatch, client)

    reporter.report_exception_sync(_raise_known(), source="api")

    assert len(client.post_calls) == 0
    assert len(client.comment_calls) == 0
    with Session(isolated_db) as s:
        assert s.exec(select(AutoFixAttempt)).first() is None


def test_redaction_strips_secrets_from_body(
    monkeypatch: pytest.MonkeyPatch, isolated_db
):
    """Secrets in the exception message must not reach GitHub."""
    sentinel_token = "AKIAEXAMPLEEXAMPLEEX"
    client = _MockGitHubClient()
    _patch_gh(monkeypatch, client)

    def _raise_with_secret():
        raise RuntimeError(f"failure with token {sentinel_token}")

    try:
        _raise_with_secret()
    except BaseException as e:
        reporter.report_exception_sync(e, source="api")

    assert client.post_calls, "expected an issue open"
    body = client.post_calls[0]["json"]["body"]
    assert sentinel_token not in body, "secret leaked into GitHub issue body"

    with Session(isolated_db) as s:
        row = s.exec(select(AutoFixAttempt)).first()
        assert row is not None
        assert sentinel_token not in row.error_message
