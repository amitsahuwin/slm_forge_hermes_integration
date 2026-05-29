"""Bridge to Hermes-style skills via Ollama HTTP.

Skills are markdown files in ~/.hermes/skills/ (mirrored from .hermes-skills/).
Each skill defines a procedure as a system prompt; the LLM (qwen2.5-coder:14b
via Ollama) executes it against the provided context, returning JSON.

When Hermes CLI stabilizes for programmatic use, swap _call_ollama for
a subprocess to `hermes` and the rest of this module stays put.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("ratchet.hermes")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "qwen2.5-coder:14b")
SKILLS_DIR = Path(os.environ.get("HERMES_SKILLS_DIR", str(Path.home() / ".hermes" / "skills")))


class MutationProposal(BaseModel):
    learning_rate: float | None = Field(default=None, ge=1e-7, le=1e-2)
    batch_size: int | None = Field(default=None, ge=1, le=32)
    num_layers: int | None = Field(default=None, ge=1, le=48)
    iters: int | None = Field(default=None, ge=20, le=2000)
    max_seq_length: int | None = Field(default=None, ge=128, le=8192)
    reasoning: str = "(no reasoning provided)"
    expected_outcome: str = ""


def load_skill(name: str) -> str | None:
    """Load a skill markdown by name (without .md extension)."""
    candidate = SKILLS_DIR / f"{name}.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    # Fallback: try the repo's .hermes-skills/ for dev convenience
    repo_candidate = Path(__file__).resolve().parents[2] / ".hermes-skills" / f"{name}.md"
    if repo_candidate.exists():
        return repo_candidate.read_text(encoding="utf-8")
    log.warning("Skill %s not found in %s or .hermes-skills/", name, SKILLS_DIR)
    return None


def _call_ollama(system: str, user: str, *, expect_json: bool = True) -> str:
    """One-shot chat call to Ollama. Returns the model's response text."""
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

    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.error("Ollama call failed: %s", e)
        raise

    return r.json()["message"]["content"]


def propose_mutation(
    *,
    dataset: str,
    history: list[dict[str, Any]],
    current_best_metric: float | None,
) -> MutationProposal:
    """Ask the LLM for the next hyperparameter mutation to try.

    `history` is a list of dicts, each describing one prior iteration with
    its hyperparams, val_loss, canary_loss, was_accepted, etc.
    """
    skill = load_skill("propose_hyperparam_mutation")
    if skill is None:
        # Fallback: minimal inline prompt
        skill = (
            "You are an ML researcher. Given iteration history, propose ONE hyperparameter "
            "change as JSON: {learning_rate?, batch_size?, num_layers?, iters?, "
            "reasoning, expected_outcome}. Be conservative."
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
    log.info("Hermes raw response: %s", raw[:300])

    try:
        data = json.loads(raw)
        return MutationProposal.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("Mutation parse failed (%s) — falling back to LR halving", e)
        # Safe fallback: halve the learning rate
        return MutationProposal(
            reasoning="LLM response invalid; fell back to LR halving",
            expected_outcome="More conservative training",
        )


def healthcheck() -> tuple[bool, str]:
    """Returns (ok, message) — used by ratchet startup."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"Ollama not reachable at {OLLAMA_URL}: {e}"
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/show", json={"name": HERMES_MODEL}, timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"Model {HERMES_MODEL} not pulled: {e}. Run: ollama pull {HERMES_MODEL}"
    return True, f"Ollama OK ({HERMES_MODEL})"
