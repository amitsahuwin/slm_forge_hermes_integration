"""Open / comment on a GitHub issue for a captured exception.

Uses ``httpx`` directly (already a runtime dep) — no PyGithub. Dedupe is
done via the GitHub search API: a fingerprint sha256-prefix is embedded
in the issue body so we can find the existing issue and comment on it
instead of opening a duplicate.

ALL public functions must return cleanly even when GitHub is unreachable —
the production capture path can't afford to raise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("error_responder.github_issue")

_GH_API = "https://api.github.com"
_USER_AGENT = "slm-forge-error-responder"


@dataclass
class IssueOutcome:
    """What happened on the GitHub side. ``url`` may be ``None`` on failure."""

    action: str  # "opened" | "commented" | "skipped" | "error"
    url: str | None = None
    error: str | None = None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }


def _search_existing(
    *, repo: str, token: str, fp_short: str, client: httpx.Client
) -> tuple[int, str] | None:
    """Return ``(issue_number, issue_url)`` of an open issue matching the
    fingerprint, or ``None`` when no match. GitHub treats fingerprint as a
    keyword search over title + body — we embed it in the body explicitly.
    """
    q = f'repo:{repo} is:issue is:open in:body "sha256:{fp_short}"'
    try:
        r = client.get(
            f"{_GH_API}/search/issues",
            params={"q": q, "per_page": 1},
            timeout=8,
        )
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            return None
        item = items[0]
        return int(item["number"]), str(item["html_url"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("github search failed (%s) — will fall through to opening a new issue", e)
        return None


def _post_issue(
    *,
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
    client: httpx.Client,
) -> tuple[int, str] | None:
    try:
        r = client.post(
            f"{_GH_API}/repos/{repo}/issues",
            headers=_headers(token),
            json={"title": title, "body": body, "labels": labels},
            timeout=8,
        )
        r.raise_for_status()
        j = r.json()
        return int(j["number"]), str(j["html_url"])
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("github issue create failed: %s", e)
        return None


def _post_comment(
    *,
    repo: str,
    token: str,
    issue_number: int,
    body: str,
    client: httpx.Client,
) -> bool:
    try:
        r = client.post(
            f"{_GH_API}/repos/{repo}/issues/{issue_number}/comments",
            headers=_headers(token),
            json={"body": body},
            timeout=8,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.warning("github comment failed on issue #%d: %s", issue_number, e)
        return False


def open_or_comment_issue(
    *,
    repo: str,
    token: str,
    title: str,
    body: str,
    fingerprint: str,
    labels: list[str] | None = None,
    client: httpx.Client | None = None,
) -> IssueOutcome:
    """Post a fingerprinted GitHub issue (or comment on an existing one).

    Always returns; never raises. ``client`` lets tests inject a mocked
    ``httpx.Client``.
    """
    fp_short = fingerprint[:12]
    own_client = client is None
    c = client or httpx.Client(headers=_headers(token))
    try:
        existing = _search_existing(repo=repo, token=token, fp_short=fp_short, client=c)
        if existing is not None:
            issue_number, url = existing
            ok = _post_comment(
                repo=repo,
                token=token,
                issue_number=issue_number,
                body=body,
                client=c,
            )
            if ok:
                return IssueOutcome(action="commented", url=url)
            return IssueOutcome(action="error", url=url, error="comment failed")

        opened = _post_issue(
            repo=repo,
            token=token,
            title=title,
            body=body,
            labels=labels or ["auto-error-report"],
            client=c,
        )
        if opened is None:
            return IssueOutcome(action="error", error="open failed")
        _, url = opened
        return IssueOutcome(action="opened", url=url)
    finally:
        if own_client:
            c.close()


def render_issue_body(
    *,
    fingerprint: str,
    service: str,
    api_version: str,
    python_version: str,
    os_label: str,
    correlation_ids: dict[str, str],
    redacted_traceback: str,
    occurrence_count: int,
    occurrences: list[str],  # ISO-8601 timestamps
) -> str:
    """Compose the GitHub issue body. Markdown; safe to render in the search
    snippet (the fingerprint hash anchors dedupe queries)."""
    correlation_md = "\n".join(
        f"- **{k}**: `{v}`" for k, v in correlation_ids.items() if v
    ) or "_(none)_"
    occurrences_md = "\n".join(f"- `{ts}`" for ts in occurrences) or "_(none)_"
    return (
        f"## Fingerprint\n"
        f"`sha256:{fingerprint[:12]}`\n"
        f"<!-- fingerprint: sha256:{fingerprint} -->\n\n"
        f"## Environment\n"
        f"- **service**: `{service}`\n"
        f"- **api_version**: `{api_version}`\n"
        f"- **python**: `{python_version}`\n"
        f"- **os**: `{os_label}`\n\n"
        f"## Correlation IDs\n"
        f"{correlation_md}\n\n"
        f"## Occurrences ({occurrence_count})\n"
        f"{occurrences_md}\n\n"
        f"## Traceback (redacted)\n"
        f"```text\n{redacted_traceback}\n```\n"
    )


def render_comment_body(
    *,
    fingerprint: str,
    occurrence_timestamp: str,
    correlation_ids: dict[str, str],
) -> str:
    """A second-occurrence comment is intentionally lighter — the original
    issue already carries the traceback. We're recording incidence."""
    correlation_md = ", ".join(f"`{k}={v}`" for k, v in correlation_ids.items() if v)
    return (
        f"_Another occurrence (fingerprint `sha256:{fingerprint[:12]}`)._\n\n"
        f"- **at**: `{occurrence_timestamp}`\n"
        + (f"- **correlation**: {correlation_md}\n" if correlation_md else "")
    )
