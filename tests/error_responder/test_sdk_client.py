"""``sdk_client.run_sdk_proposal`` must forward the configured model so
the SDK targets the LiteLLM alias (e.g. ``ollama/qwen3:30b-a3b``) instead
of falling through to the SDK's default. ``test_autofix.py`` already
monkey-patches ``run_sdk_proposal`` end-to-end; this file isolates the
options-construction path so a future change to the SDK signature
trips a test, not production traffic.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.error_responder import sdk_client


class _FakeClient:
    """Stand-in for ClaudeSDKClient with just the methods sdk_client uses."""

    last_options = None
    last_prompt = None

    def __init__(self, *, options):
        type(self).last_options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def query(self, prompt):
        type(self).last_prompt = prompt

    async def receive_response(self):
        # Emit one minimal AssistantMessage carrying a JSON manifest so
        # parse_response succeeds.
        from claude_agent_sdk import AssistantMessage, TextBlock

        yield AssistantMessage(
            content=[
                TextBlock(
                    text=(
                        "```json\n"
                        '{"source_files": ["lib.py"], '
                        '"test_path": "tests/regression/auto_fix/test_x.py", '
                        '"test_content_brief": "stub"}'
                        "\n```"
                    )
                )
            ],
            model="stub-model",
        )


@pytest.mark.asyncio
async def test_run_sdk_proposal_forwards_model_into_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from claude_agent_sdk import ClaudeAgentOptions

    captured: dict[str, object] = {}

    def fake_options_factory(**kwargs):
        captured.update(kwargs)
        return ClaudeAgentOptions(**kwargs)

    monkeypatch.setattr(sdk_client, "_resolve_model", lambda: "anthropic/qwen3-test")
    # Patch the imports inside run_sdk_proposal — the function does
    # `from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ...`
    # at call time, so we patch the module's top-level entries.
    import claude_agent_sdk as _sdk

    monkeypatch.setattr(_sdk, "ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr(_sdk, "ClaudeAgentOptions", fake_options_factory)

    proposal = await sdk_client.run_sdk_proposal(
        prompt="ignored",
        cwd=tmp_path,
        max_turns=3,
        timeout_seconds=30,
        test_path_expected="tests/regression/auto_fix/test_x.py",
    )

    assert captured.get("model") == "anthropic/qwen3-test"
    assert proposal.source_files == ["lib.py"]


def test_resolve_model_uses_settings(monkeypatch: pytest.MonkeyPatch):
    """``sdk_client._resolve_model`` reads the value from settings so a
    LiteLLM alias swap is one env-var away from changing what the SDK calls."""
    from packages.error_responder import config as _config

    monkeypatch.setenv("DEPLOYMENT_MODE", "development")
    monkeypatch.setenv("AUTOFIX_ENABLED", "false")
    monkeypatch.setenv("AUTOFIX_MODEL", "anthropic/some-alias")
    _config.reset_settings_cache()

    try:
        assert sdk_client._resolve_model() == "anthropic/some-alias"
    finally:
        _config.reset_settings_cache()


# Run an event loop helper so pytest-asyncio isn't strictly required for the
# sync test above to coexist.
def _aio_run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
