"""Thin wrapper around ``claude_agent_sdk`` for the auto-fix flow.

We import the SDK lazily so the production-mode path (PR-A) never pays
the import cost. The wrapper is small on purpose — it owns the prompt
template + result parsing, and that's it. Tests monkeypatch
``run_sdk_proposal`` to inject a synthetic ``AutoFixProposal`` without
loading the real SDK or hitting the Anthropic API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("error_responder.sdk_client")


@dataclass
class AutoFixProposal:
    """What the SDK returns after a successful loop.

    ``source_files`` is the list of files the SDK Edit-ed inside the
    project (relative paths). ``test_path`` is the new pytest file the
    SDK wrote under ``tests/regression/auto_fix/``. ``test_content`` is
    a snapshot of the test body — used by the test-quality gate so we
    can re-apply just the test against pre-fix code.

    ``raw_response`` is the full assistant text (last message), retained
    for debugging and persistence in ``AutoFixAttempt.diff``.
    """

    source_files: list[str]
    test_path: str | None
    test_content: str | None
    raw_response: str


_PROMPT = textwrap.dedent(
    """
    You are a regression-fix bot. A bug occurred in the SLM-Forge codebase.

    Repo root: {repo_root}
    Top project frame: {file}:{line} in function `{func}`
    Exception: {exc_type}: {error_message}

    Traceback (redacted, secrets scrubbed):
    ```
    {traceback}
    ```

    File excerpt (around the raise site):
    ```python
    {snippet}
    ```

    Constraints (NON-NEGOTIABLE):
      - Generate the MINIMAL fix that resolves this exception.
      - Generate a pytest test under
        `tests/regression/auto_fix/test_{fp12}.py` that REPRODUCES the
        bug (it must FAIL against the pre-fix code and PASS once the
        fix is applied).
      - Do NOT modify any file under: {denylist}
      - Do NOT touch anything under `tests/` other than the new file
        named above.
      - Do NOT create files outside the project root.

    Tools you may use: Read, Edit, Bash. Use Edit to apply your changes
    in-place — do NOT print diffs in your response.

    After all edits, output (and ONLY this) a fenced JSON block:

    ```json
    {{
      "source_files": ["<repo-relative paths of source files you edited>"],
      "test_path": "tests/regression/auto_fix/test_{fp12}.py",
      "test_content_brief": "<one-sentence description of the test>"
    }}
    ```
    """
).strip()


def _read_snippet(repo_root: Path, file_path: str, line: int, *, context: int = 30) -> str:
    """Best-effort read of ±N lines around the raise site. Used to give the
    SDK enough context to fix without slurping the whole file."""
    full = repo_root / file_path
    if not full.exists():
        return ""
    try:
        text = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, line - context - 1)
    end = min(len(text), line + context)
    return "\n".join(text[start:end])


def render_prompt(
    *,
    repo_root: Path,
    file_path: str,
    line: int,
    func: str,
    exc_type: str,
    error_message: str,
    traceback: str,
    fp12: str,
    denylist: tuple[str, ...],
) -> str:
    """Compose the prompt text — exposed for tests."""
    snippet = _read_snippet(repo_root, file_path, line) or "(file not found)"
    return _PROMPT.format(
        repo_root=str(repo_root),
        file=file_path,
        line=line,
        func=func,
        exc_type=exc_type,
        error_message=error_message[:600],  # keep prompt size bounded
        traceback=traceback[:6_000],
        snippet=snippet,
        fp12=fp12,
        denylist=", ".join(denylist),
    )


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_response(raw: str) -> dict:
    """Extract the fenced JSON block from the assistant's final message."""
    if not raw:
        raise ValueError("empty SDK response")
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        # Fallback: maybe the assistant emitted raw JSON without fences.
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError as e:
            raise ValueError(f"no JSON block found in SDK response: {e}") from e
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"SDK JSON block malformed: {e}") from e


def _resolve_model() -> str:
    """Return the model alias to send to ``ClaudeAgentOptions(model=)``.

    Reads ``settings.autofix_model`` (env: ``AUTOFIX_MODEL``) so a LiteLLM
    alias swap is a one-env-var change. Falls back to the SDK default when
    settings can't be loaded (e.g. the reporter is misconfigured) — the
    SDK then 401s, but we don't crash the import.
    """
    try:
        from packages.error_responder import config as _config

        return _config.get_settings().autofix_model
    except Exception:
        return "anthropic/claude-3-5-sonnet-20241022"


async def run_sdk_proposal(
    *,
    prompt: str,
    cwd: Path,
    max_turns: int,
    timeout_seconds: int,
    test_path_expected: str,
) -> AutoFixProposal:
    """Drive the SDK to a fix. ``cwd`` MUST be the sandbox worktree path.

    The SDK applies file edits in-place inside ``cwd``; the orchestrator
    is what captures the resulting git diff afterwards. We only need the
    JSON manifest from the assistant's last message.

    Raises ``TimeoutError`` on wall-clock cap; ``RuntimeError`` if the
    SDK exits without a parseable JSON manifest.
    """
    # Lazy import so PR-A consumers don't pay the SDK import cost.
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash"],
        permission_mode="acceptEdits",
        cwd=str(cwd),
        max_turns=max_turns,
        model=_resolve_model(),
    )

    last_assistant_text = ""

    async def _drive() -> str:
        nonlocal last_assistant_text
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            last_assistant_text = block.text
        return last_assistant_text

    try:
        raw = await asyncio.wait_for(_drive(), timeout=timeout_seconds)
    except asyncio.TimeoutError as e:  # noqa: UP041
        raise TimeoutError(
            f"SDK timed out after {timeout_seconds}s — likely runaway loop"
        ) from e

    if not raw.strip():
        raise RuntimeError("SDK exited without an assistant text response")

    try:
        manifest = parse_response(raw)
    except ValueError as e:
        raise RuntimeError(f"SDK response parse failed: {e}") from e

    source_files = [str(p) for p in (manifest.get("source_files") or []) if p]
    test_path = manifest.get("test_path") or test_path_expected

    # Snapshot the new test on disk so the quality gate can re-apply it.
    test_content: str | None = None
    test_full = cwd / test_path
    if test_full.exists():
        try:
            test_content = test_full.read_text(encoding="utf-8")
        except OSError:
            test_content = None

    return AutoFixProposal(
        source_files=source_files,
        test_path=test_path,
        test_content=test_content,
        raw_response=raw,
    )


def sdk_available() -> bool:
    """Cheap probe — ``True`` iff ``claude_agent_sdk`` is installed."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def env_ready_for_sdk() -> bool:
    """Whether the SDK has at least one credential it can use."""
    return bool(
        os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    )
