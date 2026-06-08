"""Hermes Agent status endpoints.

Surfaces a real-time view of the autoresearch stack to the Dashboard:
  • Ollama reachability + model availability (via hermes_bridge.healthcheck)
  • Ratchet worker liveness (heartbeat-driven, in-memory)
  • Skills installed on disk (.hermes-skills/ + HERMES_SKILLS_DIR)

This module deliberately avoids new DB tables — worker liveness is tracked
in a module-level dict that resets on API restart. Stale entries (>30s)
are treated as "worker down".
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from packages.ratchet.hermes_bridge import (
    HERMES_MODEL,
    SKILLS_DIR,
    healthcheck,
)

router = APIRouter()

# ─── Worker liveness (in-memory, per API process) ─────────────────────────────

WORKER_STALE_AFTER = timedelta(seconds=30)

# {"ratchet": {"last_seen": datetime, "version": "0.6.0"}}
_worker_registry: dict[str, dict[str, object]] = {}


# ─── Skills discovery ─────────────────────────────────────────────────────────

# In container: /app is the project root; on host it's the repo checkout.
_PROJECT_ROOT_CANDIDATES = [
    Path("/app"),
    Path(__file__).resolve().parents[3],
]


def _project_root() -> Path:
    for candidate in _PROJECT_ROOT_CANDIDATES:
        if (candidate / ".hermes-skills").exists():
            return candidate
    # Fallback: the parents[3] guess, even if the dir is missing
    return Path(__file__).resolve().parents[3]


def _list_skills() -> tuple[list[str], str]:
    """Return (skill_basenames, skills_dir_label).

    Looks in two places and dedupes:
      1. SKILLS_DIR (defaults to ~/.hermes/skills) — what the bridge actually loads
      2. Repo-level .hermes-skills/  — the source of truth in the repo
    """
    found: set[str] = set()
    label_parts: list[str] = []

    repo_skills = _project_root() / ".hermes-skills"
    if repo_skills.exists():
        label_parts.append(str(repo_skills))
        for p in repo_skills.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found.add(p.stem)

    if SKILLS_DIR.exists() and SKILLS_DIR != repo_skills:
        label_parts.append(str(SKILLS_DIR))
        for p in SKILLS_DIR.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            found.add(p.stem)

    label = " | ".join(label_parts) if label_parts else str(SKILLS_DIR)
    return sorted(found), label


# ─── Schemas ──────────────────────────────────────────────────────────────────


class HermesStatus(BaseModel):
    ollama_reachable: bool
    model: str
    model_available: bool
    message: str
    worker_running: bool
    worker_last_seen: str | None
    skills_dir: str
    skills_installed: list[str]


class Heartbeat(BaseModel):
    worker: str
    version: str = ""


class HeartbeatAck(BaseModel):
    ok: bool
    worker: str
    received_at: str


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=HermesStatus)
def hermes_status() -> HermesStatus:
    """Compose a single-shot status snapshot for the dashboard card."""
    ok, message = healthcheck()
    # The bridge healthcheck only returns ok=True when BOTH Ollama is reachable
    # AND the model is pulled. Disambiguate using the message text.
    if ok:
        ollama_reachable = True
        model_available = True
    else:
        # "Ollama not reachable" ⇒ infra down; otherwise Ollama is up but model is missing.
        ollama_reachable = "not reachable" not in message.lower()
        model_available = False

    entry = _worker_registry.get("ratchet")
    worker_running = False
    worker_last_seen: str | None = None
    if entry is not None:
        last_seen = entry["last_seen"]
        assert isinstance(last_seen, datetime)
        worker_last_seen = last_seen.isoformat()
        worker_running = (datetime.now(UTC) - last_seen) < WORKER_STALE_AFTER

    skills, skills_dir_label = _list_skills()

    return HermesStatus(
        ollama_reachable=ollama_reachable,
        model=HERMES_MODEL,
        model_available=model_available,
        message=message,
        worker_running=worker_running,
        worker_last_seen=worker_last_seen,
        skills_dir=skills_dir_label,
        skills_installed=skills,
    )


@router.post("/heartbeat", response_model=HeartbeatAck)
def hermes_heartbeat(payload: Heartbeat) -> HeartbeatAck:
    """Workers POST here every N seconds to advertise liveness."""
    now = datetime.now(UTC)
    _worker_registry[payload.worker] = {
        "last_seen": now,
        "version": payload.version or "unknown",
    }
    return HeartbeatAck(ok=True, worker=payload.worker, received_at=now.isoformat())
