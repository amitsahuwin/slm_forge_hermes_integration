"""PR-3 — translate raw API errors into plain-English remediation.

Wraps the ``error_remedy`` Hermes skill behind a tight latency budget so the
user's request never waits more than a few seconds on the LLM. Caller pattern:

    remedy = await translate_error(error_msg, context={"endpoint": "..."})
    raise HTTPException(422, detail={"message": error_msg, "remedy": remedy})

The contract on every failure mode is identical: **never raise**. Return
``None`` when the remedy isn't available so the original error response
proceeds unmodified.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from typing import Any

log = logging.getLogger("api.remedy")


def _enabled() -> bool:
    return os.environ.get("HERMES_REMEDY_ENABLED", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _timeout_s() -> float:
    return float(os.environ.get("HERMES_REMEDY_TIMEOUT_S", "4"))


def _cache_key(message: str, context: dict[str, Any] | None) -> str:
    """Stable cache key — sorted JSON keeps the hash deterministic across runs."""
    ctx_str = json.dumps(context or {}, sort_keys=True, default=str)
    return hashlib.sha256(f"{message}\n{ctx_str}".encode()).hexdigest()


# Insertion-ordered dict acts as a tiny LRU. ``functools.lru_cache`` doesn't
# work here because the cached value depends on an async call we can't make
# from a hashable function signature.
_REMEDY_CACHE: dict[str, str | None] = {}
_REMEDY_CACHE_ORDER: list[str] = []
_REMEDY_CACHE_MAX = 256


def _cache_get(key: str) -> tuple[bool, str | None]:
    """Returns (hit, value)."""
    if key in _REMEDY_CACHE:
        return True, _REMEDY_CACHE[key]
    return False, None


def _cache_set(key: str, value: str | None) -> None:
    if key not in _REMEDY_CACHE:
        _REMEDY_CACHE_ORDER.append(key)
        if len(_REMEDY_CACHE_ORDER) > _REMEDY_CACHE_MAX:
            evict = _REMEDY_CACHE_ORDER.pop(0)
            _REMEDY_CACHE.pop(evict, None)
    _REMEDY_CACHE[key] = value


def clear_cache() -> None:
    """Test helper — wipe the cache between cases."""
    _REMEDY_CACHE.clear()
    _REMEDY_CACHE_ORDER.clear()


async def translate_error(
    message: str,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Ask Hermes for a 1-3 sentence plain-English remedy.

    Returns:
        - The remedy string on success.
        - ``None`` when disabled, timed out, on Hermes error, or on any
          unexpected exception. The caller MUST still raise its original
          HTTPException — this function never modifies the error path.
    """
    if not _enabled():
        return None
    if not message:
        return None

    key = _cache_key(message, context)
    hit, cached = _cache_get(key)
    if hit:
        return cached

    payload = {"error_message": message, "context": context or {}}

    try:
        # The skill returns markdown (not JSON). ``run_skill`` is sync (uses
        # synchronous httpx under the hood); offload to a thread so we don't
        # block the event loop and so ``asyncio.wait_for`` can enforce a hard
        # cap that survives Hermes hangs.
        remedy = await asyncio.wait_for(
            asyncio.to_thread(_invoke_skill, payload),
            timeout=_timeout_s(),
        )
    except TimeoutError:
        log.info("remedy timed out after %.1fs — proceeding without it", _timeout_s())
        # Don't cache timeouts — Ollama may have just been slow this once.
        return None
    except Exception as e:
        log.warning("remedy generation failed: %s: %s", type(e).__name__, e)
        return None

    text = (remedy or "").strip()
    if not text:
        return None

    _cache_set(key, text)
    return text


def _invoke_skill(payload: dict[str, Any]) -> str:
    """Thin sync wrapper around ``run_skill`` so ``asyncio.to_thread`` can
    target it. Kept in this module so monkeypatching in tests is local."""
    from packages.ratchet.hermes_bridge import run_skill

    return run_skill("error_remedy", payload, expect_json=False)
