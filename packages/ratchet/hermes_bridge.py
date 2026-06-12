"""Bridge to Hermes-style skills via Ollama HTTP.

Skills are markdown files in ~/.hermes/skills/ (mirrored from .hermes-skills/).
Each skill defines a procedure as a system prompt; the LLM executes it via
Ollama and returns JSON.

Config (overridable via .env):
  OLLAMA_URL          default http://localhost:11434
  HERMES_MODEL        default qwen3:30b-a3b
  HERMES_SKILLS_DIR   default ~/.hermes/skills
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

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


class MutationProposal(BaseModel):
    learning_rate: float | None = Field(default=None, ge=1e-7, le=1e-2)
    batch_size: int | None = Field(default=None, ge=1, le=32)
    num_layers: int | None = Field(default=None, ge=1, le=48)
    iters: int | None = Field(default=None, ge=20, le=2000)
    max_seq_length: int | None = Field(default=None, ge=128, le=8192)
    reasoning: str = "(no reasoning provided)"
    expected_outcome: str = ""


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

    import time as _time

    start = _time.monotonic()
    response_text = ""
    error_msg: str | None = None
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        response_text = r.text
    except httpx.HTTPError as e:
        error_msg = f"{type(e).__name__}: {e}"
        log.error("Ollama call failed: %s", e)
        _record_trace(
            source=trace_source,
            request_body=payload,
            response_text="",
            error=error_msg,
            duration_ms=int((_time.monotonic() - start) * 1000),
        )
        raise

    duration_ms = int((_time.monotonic() - start) * 1000)
    _record_trace(
        source=trace_source,
        request_body=payload,
        response_text=response_text,
        error=None,
        duration_ms=duration_ms,
    )
    return r.json()["message"]["content"]


def _record_trace(
    *,
    source: str,
    request_body: dict[str, Any],
    response_text: str,
    error: str | None,
    duration_ms: int,
) -> None:
    """Best-effort persistence of a single Ollama call.

    Runs inside the API container (sqlmodel + DB available) AND inside the
    workers (no DB session — they don't import sqlmodel). The import is
    lazy + wrapped so worker callers don't crash when the trace table isn't
    reachable.
    """
    try:
        import json as _json

        from sqlmodel import Session as _Session

        from apps.api.models.hermes_trace import HermesTrace
        from apps.api.services.db import engine

        with _Session(engine) as db:
            db.add(
                HermesTrace(
                    source=source,
                    model=HERMES_MODEL,
                    request_body=_json.dumps(request_body, ensure_ascii=False),
                    response_body=response_text or "",
                    error=error,
                    duration_ms=duration_ms,
                )
            )
            db.commit()
    except Exception as e:  # noqa: BLE001
        # Workers call _call_ollama without DB access — that's expected.
        # Don't let trace persistence affect functional behavior.
        log.debug("trace record skipped (%s)", e)


def propose_mutation(
    *,
    dataset: str,
    history: list[dict[str, Any]],
    current_best_metric: float | None,
) -> MutationProposal:
    """Ask the LLM for the next hyperparameter mutation to try."""
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

    raw = _call_ollama(skill, user_msg, expect_json=True)
    log.info("Hermes raw response (first 300 chars): %s", raw[:300])

    try:
        data = json.loads(raw)
        return MutationProposal.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("Mutation parse failed (%s) — falling back to LR halving", e)
        return MutationProposal(
            reasoning=f"LLM response invalid ({e}); fell back to LR halving",
            expected_outcome="More conservative training",
        )


def _list_available_models() -> list[str]:
    """Best-effort list of pulled Ollama models for friendly error messages."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


def healthcheck() -> tuple[bool, str]:
    """Returns (ok, message)."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"Ollama not reachable at {OLLAMA_URL}: {e}"

    try:
        r = httpx.post(f"{OLLAMA_URL}/api/show", json={"name": HERMES_MODEL}, timeout=5)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        available = _list_available_models()
        msg_lines = [
            f"Model {HERMES_MODEL!r} is not pulled in Ollama.",
            f"  Ollama error: {e}",
        ]
        if available:
            msg_lines.append(f"  Currently pulled models: {', '.join(available)}")
            msg_lines.append(
                f"  To use one of those, set HERMES_MODEL=<name> in .env and re-run."
            )
        else:
            msg_lines.append("  No models pulled yet. Pull one with: ollama pull <name>")
        return False, "\n".join(msg_lines)

    return True, f"Ollama OK ({HERMES_MODEL} @ {OLLAMA_URL})"
