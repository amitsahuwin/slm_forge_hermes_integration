"""Regression: the async ingest endpoints must not block the event loop.

``POST /api/v1/ingest/file`` and ``/preview`` run a synchronous, potentially
multi-minute Ollama conversion (``_convert`` → ``convert_via_ollama`` → a
blocking ``httpx.post``). If that call runs inline on the single asyncio event
loop, the whole API freezes and every worker's 5s heartbeat POST times out.

These tests pin the fix: ``_convert`` must be offloaded off the event loop
(``asyncio.to_thread``) so a concurrent coroutine keeps making progress while a
conversion is in flight. The stub blocks until an async "releaser" coroutine
signals it — which can only happen if the loop is free.
"""
from __future__ import annotations

import asyncio
import io
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import UploadFile

from apps.api.routers import ingest_v2
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import identity_paths as ip_module

_RECORDS = [
    {"messages": [
        {"role": "user", "content": f"q{i}"},
        {"role": "assistant", "content": f"a{i}"},
    ]}
    for i in range(12)
]


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SLM_FORGE_AUTH_ENABLED", "false")
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.setattr(ip_module, "DATASETS_ROOT", tmp_path / "datasets")
    yield
    auth_settings_module.get_auth_settings.cache_clear()


def _req() -> object:
    class _R:
        class state:  # noqa: N801 - mimic request.state
            user = None

    return _R()


def _upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


async def _drive_with_blocking_convert(coro_factory) -> None:
    """Run an endpoint coroutine whose ``_convert`` blocks until an async
    releaser signals it. Fails if the event loop is blocked (the releaser can
    never run, and the stub's failsafe wait expires)."""
    convert_started = threading.Event()
    release = threading.Event()

    def _blocking_convert(content: bytes, filename: str, force_ollama: bool = False):
        convert_started.set()
        # If the loop is free, the releaser runs and sets this promptly.
        if not release.wait(timeout=2.0):
            raise AssertionError("event loop was blocked: releaser never ran")
        return _RECORDS, "jsonl_chat", "ollama", []

    async def _releaser() -> None:
        while not convert_started.is_set():
            await asyncio.sleep(0.01)
        release.set()

    import apps.api.routers.ingest_v2 as mod

    original = mod._convert
    mod._convert = _blocking_convert  # type: ignore[assignment]
    try:
        await asyncio.gather(coro_factory(), _releaser())
    finally:
        mod._convert = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_ingest_file_does_not_block_event_loop(env: None) -> None:
    data = b'{"prompt":"p","completion":"c"}\n'

    async def _call():
        return await ingest_v2.ingest_file(
            request=_req(),
            name="nonblock",
            file=_upload(data, "x.jsonl"),
            force_ollama=True,
        )

    await _drive_with_blocking_convert(_call)


@pytest.mark.asyncio
async def test_preview_file_does_not_block_event_loop(env: None) -> None:
    data = b'{"prompt":"p","completion":"c"}\n'

    async def _call():
        return await ingest_v2.preview_file(
            file=_upload(data, "x.jsonl"), force_ollama=True
        )

    await _drive_with_blocking_convert(_call)