"""Service-account HTTP client for SLM-Forge workers.

The host workers (trainer / ratchet / exporter) talk to the FastAPI control
plane via plain ``httpx`` calls. When ``SLM_FORGE_AUTH_ENABLED=true`` the API
enforces JWT + OPA on every request, but the workers don't have an OIDC
login flow — they run as plain Python processes on the user's Mac.

The solution is a **shared service token**:

  - ``SLM_FORGE_SERVICE_TOKEN`` env var, set in ``.env`` (both API container
    and host workers read the same value).
  - Workers send it in the ``X-Service-Token`` header on every request.
  - The API's ``AuthMiddleware`` checks for this header BEFORE JWT validation
    and, on match, attaches a synthetic ``service`` user with admin role to
    ``request.state.user`` and short-circuits past OPA.

To make this transparent for worker code we monkey-patch ``httpx`` at worker
startup so module-level ``httpx.get / post / patch / put / delete / request``
calls — and any ``httpx.Client`` instance — pick up the header automatically.
No per-call-site edits required.

Each worker's ``__main__.py`` calls :func:`install` once at startup. After
that, every ``httpx`` call in the same process inherits the header.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("worker.api_client")

SERVICE_TOKEN = os.environ.get("SLM_FORGE_SERVICE_TOKEN", "").strip()

_HEADER_NAME = "X-Service-Token"
_PATCH_FLAG = "_slm_forge_patched"


def service_headers() -> dict[str, str]:
    """Return headers a worker should attach to every API request.

    Useful for explicit-header callers that don't go through the global
    monkey-patch (rare; most code paths should just call :func:`install`
    once and forget about it).
    """
    return {_HEADER_NAME: SERVICE_TOKEN} if SERVICE_TOKEN else {}


def install() -> None:
    """Patch ``httpx`` so every request from this process carries the
    X-Service-Token header. Idempotent.

    Logs a clear warning if the token isn't set — that's the usual reason a
    worker hits 401s in a freshly-cloned checkout.
    """
    if not SERVICE_TOKEN:
        log.warning(
            "SLM_FORGE_SERVICE_TOKEN is empty. If the API has "
            "SLM_FORGE_AUTH_ENABLED=true, every worker request will be 401. "
            "Set the token in .env so both API and workers see the same value."
        )
        return

    if getattr(httpx, _PATCH_FLAG, False):
        return

    _patch_module_level(httpx)
    _patch_client_class(httpx.Client)
    _patch_client_class(httpx.AsyncClient)
    setattr(httpx, _PATCH_FLAG, True)
    log.info("httpx patched with %s header for service account", _HEADER_NAME)


def _merge_headers(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Mutate kwargs to ensure our header is present; preserve any caller headers."""
    headers = dict(kwargs.get("headers") or {})
    # setdefault so a caller can still override per-call if they really want to.
    headers.setdefault(_HEADER_NAME, SERVICE_TOKEN)
    kwargs["headers"] = headers
    return kwargs


def _patch_module_level(mod: Any) -> None:
    """Wrap httpx.get / post / patch / put / delete / head / request."""
    for name in ("get", "post", "patch", "put", "delete", "head", "request"):
        orig = getattr(mod, name, None)
        if orig is None:
            continue

        def make_wrapper(_orig: Any, _name: str) -> Any:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return _orig(*args, **_merge_headers(kwargs))

            wrapper.__name__ = _name
            wrapper.__doc__ = (
                f"SLM-Forge service-token wrapper around httpx.{_name}; "
                "injects X-Service-Token automatically."
            )
            return wrapper

        setattr(mod, name, make_wrapper(orig, name))


def _patch_client_class(klass: Any) -> None:
    """Inject default headers into every new Client / AsyncClient."""
    orig_init = klass.__init__

    def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
        existing = kwargs.get("headers")
        if existing is None:
            kwargs["headers"] = {_HEADER_NAME: SERVICE_TOKEN}
        elif isinstance(existing, dict):
            existing.setdefault(_HEADER_NAME, SERVICE_TOKEN)
            kwargs["headers"] = existing
        # else: caller passed a list[tuple] or httpx.Headers — leave alone.
        orig_init(self, *args, **kwargs)

    new_init.__name__ = "__init__"
    klass.__init__ = new_init
