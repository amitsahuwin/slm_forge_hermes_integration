"""Open a GitHub pull request for an auto-fix sandbox branch.

Companion to ``github_issue.py``. Dev-mode (``AUTOFIX_DEPLOY=pr``) hits
this after a successful sandbox commit + push. Uses ``httpx`` directly —
no PyGithub — to match the patterns already in ``github_issue.py``.

Dedup: a head branch can carry at most one open PR. We list open PRs
filtered by head before posting, so a re-run against the same sandbox
branch returns the existing PR URL instead of failing with
``422 Unprocessable Entity``.

ALL public functions return cleanly; the auto-fix orchestrator inspects
``PROutcome.action`` to decide whether to mark the row deployed or failed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("error_responder.github_pr")

_GH_API = "https://api.github.com"
_USER_AGENT = "slm-forge-error-responder"


@dataclass
class PROutcome:
    """What happened on the GitHub side. ``url`` is ``None`` on failure."""

    action: str  # "opened" | "exists" | "error"
    url: str | None = None
    error: str | None = None
    number: int | None = None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }


def _list_existing_pr(
    *, repo: str, token: str, head_branch: str, client: httpx.Client
) -> tuple[int, str] | None:
    """Return ``(number, html_url)`` of an open PR with the given head, or
    ``None`` when no match. GitHub expects the head as ``owner:branch``."""
    owner = repo.split("/", 1)[0]
    try:
        r = client.get(
            f"{_GH_API}/repos/{repo}/pulls",
            params={
                "state": "open",
                "head": f"{owner}:{head_branch}",
                "per_page": 1,
            },
            timeout=8,
        )
        r.raise_for_status()
        items = r.json() or []
        if not items:
            return None
        item = items[0]
        return int(item["number"]), str(item["html_url"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("github pr list failed (%s) — will attempt POST anyway", e)
        return None


def _post_pr(
    *,
    repo: str,
    token: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    client: httpx.Client,
) -> tuple[int, str] | tuple[None, str]:
    """Returns ``(number, url)`` on success or ``(None, error)`` on failure."""
    try:
        r = client.post(
            f"{_GH_API}/repos/{repo}/pulls",
            headers=_headers(token),
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            },
            timeout=12,
        )
        r.raise_for_status()
        j = r.json()
        return int(j["number"]), str(j["html_url"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return None, f"{type(e).__name__}: {e}"


def open_pull_request(
    *,
    repo: str,
    token: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    fingerprint: str,
    client: httpx.Client | None = None,
) -> PROutcome:
    """Open a PR (or return the existing one) for ``head_branch``.

    ``fingerprint`` is retained for logging; PR-level dedupe uses
    ``head_branch`` because GitHub guarantees uniqueness of open PRs per
    head. Never raises.
    """
    own_client = client is None
    c = client or httpx.Client(headers=_headers(token))
    try:
        existing = _list_existing_pr(
            repo=repo, token=token, head_branch=head_branch, client=c
        )
        if existing is not None:
            number, url = existing
            log.info(
                "github pr already open for head=%s — fp=%s url=%s",
                head_branch,
                fingerprint[:12],
                url,
            )
            return PROutcome(action="exists", url=url, number=number)

        result = _post_pr(
            repo=repo,
            token=token,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
            client=c,
        )
        number, url_or_err = result
        if number is None:
            log.warning("github pr open failed for head=%s: %s", head_branch, url_or_err)
            return PROutcome(action="error", error=url_or_err)
        return PROutcome(action="opened", url=url_or_err, number=number)
    finally:
        if own_client:
            c.close()


def render_pr_body(
    *,
    fingerprint: str,
    exc_type: str,
    error_message: str,
    file_target: str | None,
    correlation_ids: dict[str, str],
    redacted_traceback: str,
    test_path: str | None,
    diff_excerpt: str | None,
) -> str:
    """Compose the PR description body.

    Mirrors ``github_issue.render_issue_body`` so the structure feels
    consistent across modes; adds a fixed-by-test and diff-excerpt block
    that's specific to PR mode.
    """
    correlation_md = "\n".join(
        f"- **{k}**: `{v}`" for k, v in correlation_ids.items() if v
    ) or "_(none)_"
    fixed_by = f"`{test_path}`" if test_path else "_(no regression test recorded)_"
    diff_section = (
        f"\n## Diff (truncated)\n```diff\n{diff_excerpt[:6_000]}\n```\n"
        if diff_excerpt
        else ""
    )
    file_md = f"`{file_target}`" if file_target else "_(unknown)_"
    return (
        f"## Auto-fix proposal\n"
        f"`sha256:{fingerprint[:12]}`\n"
        f"<!-- fingerprint: sha256:{fingerprint} -->\n\n"
        f"## Exception\n"
        f"- **type**: `{exc_type}`\n"
        f"- **message**: {error_message[:500]}\n"
        f"- **file**: {file_md}\n\n"
        f"## Regression test\n"
        f"{fixed_by}\n\n"
        f"## Correlation IDs\n"
        f"{correlation_md}\n\n"
        f"## Traceback (redacted)\n"
        f"```text\n{redacted_traceback[:6_000]}\n```\n"
        f"{diff_section}"
    )
