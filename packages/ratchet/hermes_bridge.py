"""Bridge to Hermes-style skills via Ollama HTTP.

Skills are markdown files in ~/.hermes/skills/ (mirrored from .hermes-skills/).
Each skill defines a procedure as a system prompt; the LLM executes it via
Ollama and returns JSON.

Config (overridable via .env):
  OLLAMA_URL                       default http://localhost:11434
  HERMES_MODEL                     default qwen3:30b-a3b
  HERMES_SKILLS_DIR                default ~/.hermes/skills
  HERMES_MAX_RETRIES               default 3 (A1)
  HERMES_OLLAMA_TIMEOUT_S          default 300 (A1)
  HERMES_RETRY_BACKOFF_MULT_S      default 0.5 (A1)
  HERMES_MAX_PROPOSAL_FAILURES     default 3 (A2)
  HERMES_LOG_PAYLOADS              default false (A3)
  HERMES_TRACE_STORE_PAYLOADS      default true  (A3)
  HERMES_TRACE_REDACT_SOURCES      default redact dataset_synth / ingest / auto-label / QA (A3)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

# Load .env from the project root, if present (no-op if dotenv not installed)
try:
    from dotenv import load_dotenv

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass

log = logging.getLogger("ratchet.hermes")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "qwen3:30b-a3b")
SKILLS_DIR = Path(
    os.environ.get("HERMES_SKILLS_DIR", str(Path.home() / ".hermes" / "skills"))
)

# PR-1 A1 — only retry transient network errors + the standard "back off and retry" HTTP codes.
_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})

# PR-1 A3 — sources whose request/response bodies must never be persisted verbatim
# because they routinely contain user dataset rows (PII / proprietary text).
_DEFAULT_REDACT_SOURCES = (
    "skill:dataset_synth,skill:ingest_dataset,"
    "skill:auto_label_unlabeled,skill:data_quality_review"
)


class MutationProposal(BaseModel):
    learning_rate: float | None = Field(default=None, ge=1e-7, le=1e-2)
    batch_size: int | None = Field(default=None, ge=1, le=32)
    num_layers: int | None = Field(default=None, ge=1, le=48)
    iters: int | None = Field(default=None, ge=20, le=2000)
    max_seq_length: int | None = Field(default=None, ge=128, le=8192)
    reasoning: str = "(no reasoning provided)"
    expected_outcome: str = ""


class MutationProposalError(RuntimeError):
    """PR-1 A2 — raised when the LLM response can't be parsed into a valid
    ``MutationProposal``. The previous behavior was to fabricate a
    do-nothing proposal whose ``reasoning`` claimed "LR halving" but
    actually changed no fields — a silent fallback that violated CLAUDE.md
    rule 16. Callers (notably ``loop.run_session``) must now catch this
    explicitly and decide whether to skip the iteration, abort the
    session, or implement a real conservative fallback.
    """


def load_skill(name: str) -> str | None:
    """Load a skill markdown by name (no .md extension)."""
    candidate = SKILLS_DIR / f"{name}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    repo_candidate = Path(__file__).resolve().parents[2] / ".hermes-skills" / f"{name}.md"
    if repo_candidate.exists():
        return repo_candidate.read_text(encoding="utf-8")
    log.warning("Skill %s not found in %s or .hermes-skills/", name, SKILLS_DIR)
    return None


def _is_retryable_exc(exc: BaseException) -> bool:
    """PR-1 A1 — predicate for tenacity's ``retry_if_exception``."""
    if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUSES
    return False


def _redact_sources() -> frozenset[str]:
    raw = os.environ.get("HERMES_TRACE_REDACT_SOURCES", _DEFAULT_REDACT_SOURCES)
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _store_payloads() -> bool:
    return os.environ.get("HERMES_TRACE_STORE_PAYLOADS", "true").lower() not in (
        "false",
        "0",
        "no",
    )


def _log_payloads() -> bool:
    return os.environ.get("HERMES_LOG_PAYLOADS", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _maybe_redact_body(source: str, body: str) -> str:
    """PR-1 A3 — apply both kill-switches: redact-source list and global store-off."""
    if not body:
        return body
    if not _store_payloads():
        return f"<redacted: HERMES_TRACE_STORE_PAYLOADS=false, len={len(body)}>"
    if source in _redact_sources():
        return f"<redacted: source in HERMES_TRACE_REDACT_SOURCES, len={len(body)}>"
    return body


def _log_response_meta(content: str, duration_ms: int, source: str) -> None:
    """PR-1 A3 — structured non-payload log so the response body never lands in logs.

    When ``HERMES_LOG_PAYLOADS=true`` is set (developer debugging only), the
    first 300 chars are emitted at DEBUG. Default-off keeps PII out of logs.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    log.info(
        "hermes_response source=%s len=%d sha256=%s duration_ms=%d",
        source,
        len(content),
        digest,
        duration_ms,
    )
    if _log_payloads():
        log.debug("hermes_response_body[%s] %s", source, content[:300])


def _call_ollama(
    system: str,
    user: str,
    *,
    expect_json: bool = True,
    trace_source: str = "unknown",
) -> str:
    payload: dict[str, Any] = {
        "model": HERMES_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.4},
    }
    if expect_json:
        payload["format"] = "json"

    timeout_s = float(os.environ.get("HERMES_OLLAMA_TIMEOUT_S", "300"))
    max_attempts = max(1, int(os.environ.get("HERMES_MAX_RETRIES", "3")))
    backoff_mult = float(os.environ.get("HERMES_RETRY_BACKOFF_MULT_S", "0.5"))

    retrying = Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=backoff_mult, min=backoff_mult, max=4)
        + wait_random(0, 0.5),
        retry=retry_if_exception(_is_retryable_exc),
        reraise=True,
    )

    import time as _time

    start = _time.monotonic()
    # Closure-captured counter — tenacity's ``retry_state.attempt_number`` is
    # available on the success path but not after the loop re-raises.
    stats = {"attempts": 0}

    try:
        for attempt in retrying:
            stats["attempts"] += 1
            with attempt:
                r = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout_s)
                r.raise_for_status()
                response_text = r.text
                content: str = r.json()["message"]["content"]
                duration_ms = int((_time.monotonic() - start) * 1000)
                _record_trace(
                    source=trace_source,
                    request_body=payload,
                    response_text=response_text,
                    error=None,
                    duration_ms=duration_ms,
                    attempts=stats["attempts"],
                )
                _log_response_meta(content, duration_ms, trace_source)
                return content
    except (
        httpx.HTTPError,
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    ) as e:
        duration_ms = int((_time.monotonic() - start) * 1000)
        error_msg = f"{type(e).__name__}: {e}"
        log.error(
            "ollama_call_failed source=%s attempts=%d error=%s",
            trace_source,
            stats["attempts"],
            error_msg,
        )
        _record_trace(
            source=trace_source,
            request_body=payload,
            response_text="",
            error=error_msg,
            duration_ms=duration_ms,
            attempts=stats["attempts"] or 1,
        )
        raise

    # Defensive: ``Retrying`` with ``reraise=True`` either returns or raises.
    raise RuntimeError("retry loop exited without returning or raising")  # pragma: no cover


def _record_trace(
    *,
    source: str,
    request_body: dict[str, Any],
    response_text: str,
    error: str | None,
    duration_ms: int,
    attempts: int = 1,
) -> None:
    """Best-effort persistence of a single Ollama call.

    Runs inside the API container (sqlmodel + DB available) AND inside the
    workers (sometimes without a DB session). The import is lazy + wrapped
    so worker callers don't crash when the trace table isn't reachable.

    PR-1 A3: request/response bodies are filtered through ``_maybe_redact_body``
    before persistence; PII-risky sources are blanked by default.
    PR-1 A4: ``tenant_id`` resolved from request contextvar or env fallback.
    """
    try:
        import json as _json

        from sqlmodel import Session as _Session

        from apps.api.models.hermes_trace import HermesTrace
        from apps.api.services.db import engine
        from apps.api.services.tenant import current_tenant

        request_body_str = _maybe_redact_body(source, _json.dumps(request_body, ensure_ascii=False))
        response_body_str = _maybe_redact_body(source, response_text or "")

        with _Session(engine) as db:
            db.add(
                HermesTrace(
                    source=source,
                    model=HERMES_MODEL,
                    request_body=request_body_str,
                    response_body=response_body_str,
                    error=error,
                    duration_ms=duration_ms,
                    attempts=attempts,
                    tenant_id=current_tenant(),
                )
            )
            db.commit()
    except Exception as e:
        # Workers call _call_ollama without DB access — that's expected.
        # Don't let trace persistence affect functional behavior.
        log.debug("trace record skipped (%s)", e)


def propose_mutation(
    *,
    dataset: str,
    history: list[dict[str, Any]],
    current_best_metric: float | None,
) -> MutationProposal:
    """Ask the LLM for the next hyperparameter mutation to try.

    Raises:
        MutationProposalError: when the LLM response is not parseable JSON
            or fails ``MutationProposal`` validation. The caller decides what
            to do — there is no silent fabricated fallback (PR-1 A2).
    """
    skill = load_skill("propose_hyperparam_mutation")
    if skill is None:
        skill = (
            "You are an ML researcher. Given iteration history, propose ONE "
            "hyperparameter change as JSON with keys: learning_rate, batch_size, "
            "num_layers, iters, max_seq_length (all optional), reasoning, "
            "expected_outcome. Change AT MOST TWO fields. Be conservative."
        )

    user_msg = json.dumps(
        {
            "dataset": dataset,
            "history": history,
            "current_best_metric": current_best_metric,
            "instruction": (
                "Propose the next mutation. Return JSON only. "
                "Change at most TWO hyperparameters per iteration."
            ),
        },
        default=str,
    )

    raw = _call_ollama(skill, user_msg, expect_json=True, trace_source="skill:propose_hyperparam_mutation")

    try:
        data = json.loads(raw)
        return MutationProposal.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        raise MutationProposalError(
            f"LLM response not parseable as MutationProposal: {type(e).__name__}: {e}"
        ) from e


def run_skill(
    name: str,
    payload: dict[str, Any] | str,
    *,
    expect_json: bool = False,
    fallback_system: str | None = None,
) -> str:
    """Invoke a Hermes skill by name.

    ``name`` is the filename stem under ``.hermes-skills/`` (no ``.md``). The
    skill markdown becomes the system prompt; ``payload`` becomes the user
    message (JSON-serialized if a dict). Inherits the retry / redaction /
    tenant behaviour of ``_call_ollama``.

    Args:
        name: skill stem (e.g. ``"failure_post_mortem"``).
        payload: dict or string. dicts are ``json.dumps``-ed with ``default=str``.
        expect_json: ``True`` for skills that return JSON (forces Ollama
            ``format=json``); ``False`` for markdown-producing skills like
            ``failure_post_mortem``.
        fallback_system: optional inline system prompt used when the skill
            markdown isn't on disk. Without it, a missing skill raises
            ``FileNotFoundError`` so the caller is forced to handle the gap.

    Returns:
        The raw response content (markdown for ``expect_json=False``;
        JSON-as-string otherwise — caller parses).

    Raises:
        FileNotFoundError: skill not found and no ``fallback_system`` provided.
        httpx.HTTPError: propagated from ``_call_ollama`` after retries exhaust.
    """
    skill_text = load_skill(name)
    if skill_text is None:
        if fallback_system is None:
            raise FileNotFoundError(
                f"Hermes skill {name!r} not found in {SKILLS_DIR} or .hermes-skills/"
            )
        skill_text = fallback_system

    user_msg = (
        payload if isinstance(payload, str) else json.dumps(payload, default=str)
    )
    return _call_ollama(
        skill_text,
        user_msg,
        expect_json=expect_json,
        trace_source=f"skill:{name}",
    )


def _list_available_models() -> list[str]:
    """Best-effort list of pulled Ollama models for friendly error messages."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def healthcheck() -> tuple[bool, str]:
    """Returns (ok, message). Probe-fast: not retried — caller decides cadence."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=3)
        r.raise_for_status()
    except Exception as e:
        return False, f"Ollama not reachable at {OLLAMA_URL}: {e}"

    try:
        r = httpx.post(f"{OLLAMA_URL}/api/show", json={"name": HERMES_MODEL}, timeout=5)
        r.raise_for_status()
    except Exception as e:
        available = _list_available_models()
        msg_lines = [
            f"Model {HERMES_MODEL!r} is not pulled in Ollama.",
            f"  Ollama error: {e}",
        ]
        if available:
            msg_lines.append(f"  Currently pulled models: {', '.join(available)}")
            msg_lines.append(
                "  To use one of those, set HERMES_MODEL=<name> in .env and re-run."
            )
        else:
            msg_lines.append("  No models pulled yet. Pull one with: ollama pull <name>")
        return False, "\n".join(msg_lines)

    return True, f"Ollama OK ({HERMES_MODEL} @ {OLLAMA_URL})"
