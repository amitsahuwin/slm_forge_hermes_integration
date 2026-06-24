"""LangChain tools wrapping the SLM-Forge HTTP API.

Each tool is a thin, well-typed shim over /api/v1/* endpoints. Tools that
*mutate* state (e.g., starting an experiment) are gated — they return a
``requires_confirmation`` envelope and DO NOT execute. A separate
``confirm_*`` tool performs the actual mutation once the UI confirms.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

log = logging.getLogger("chat_agent.tools")

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _auth_headers() -> dict[str, str]:
    """Build outbound auth headers for calls back into the SLM-Forge API.

    Resolution order (least-privilege first):
      1. ``bearer_token_ctx`` — the active request's verified JWT, bound
         by ``AuthMiddleware``. Forwards the user's own permissions so a
         non-admin can't reach admin endpoints through the chat agent.
      2. ``SLM_FORGE_SERVICE_TOKEN`` env — service-account bypass. Used
         in worker contexts that have no request scope.
      3. Neither — the call goes unauthenticated, matching today's dev
         workflow when auth is disabled.
    """
    from packages._log_context import bearer_token_ctx

    bound = bearer_token_ctx.get()
    if bound:
        return {"Authorization": f"Bearer {bound}"}
    svc = os.environ.get("SLM_FORGE_SERVICE_TOKEN", "")
    if svc:
        return {"X-Service-Token": svc}
    return {}


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_URL, timeout=_DEFAULT_TIMEOUT)


def _safe_get(path: str, **params: Any) -> Any:
    try:
        with _client() as c:
            r = c.get(
                path,
                params={k: v for k, v in params.items() if v is not None},
                headers=_auth_headers(),
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("GET %s failed: %s", path, e)
        return {"error": str(e), "path": path}


def _safe_post(path: str, payload: dict[str, Any]) -> Any:
    try:
        with _client() as c:
            r = c.post(path, json=payload, headers=_auth_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("POST %s failed: %s", path, e)
        return {"error": str(e), "path": path}


# ─── Read-only tools ──────────────────────────────────────────────


@tool
def list_datasets() -> list[dict[str, Any]]:
    """List all datasets available for training.

    Returns a list of {name, train_count, valid_count, has_canary, description}.
    """
    data = _safe_get("/api/v1/datasets")
    if isinstance(data, dict) and "error" in data:
        return [data]
    return [
        {
            "name": d.get("name"),
            "train_count": d.get("train_count"),
            "valid_count": d.get("valid_count"),
            "has_canary": d.get("has_canary"),
            "description": d.get("description"),
        }
        for d in data
    ]


@tool
def list_runs(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List recent training runs, optionally filtered by status.

    Args:
        status: One of queued|running|completed|failed|cancelled (optional).
        limit: Maximum number of runs to return (default 20).
    """
    data = _safe_get("/api/v1/runs", status=status, limit=limit)
    if isinstance(data, dict) and "error" in data:
        return [data]
    keep = (
        "id", "dataset", "base_model", "method", "iters", "batch_size",
        "learning_rate", "status", "final_train_loss", "final_val_loss",
        "session_id", "iteration_number", "was_accepted", "created_at",
    )
    return [{k: r.get(k) for k in keep} for r in data]


@tool
def get_run_metrics(run_id: int) -> dict[str, Any]:
    """Fetch the metric series for a run (suitable for charting).

    Returns ``{run_id, series: {metric_name: [{step, value}]}}``.
    """
    data = _safe_get(f"/api/v1/runs/{run_id}/metrics")
    if isinstance(data, dict) and "error" in data:
        return data
    series: dict[str, list[dict[str, float]]] = {}
    for m in data:
        name = m.get("name", "unknown")
        series.setdefault(name, []).append(
            {"step": m.get("step"), "value": m.get("value")}
        )
    return {"run_id": run_id, "series": series, "metric_count": len(data)}


@tool
def get_run_status(run_id: int) -> dict[str, Any]:
    """Return the current status of a run."""
    data = _safe_get(f"/api/v1/runs/{run_id}")
    if isinstance(data, dict) and "error" in data:
        return data
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "dataset": data.get("dataset"),
        "base_model": data.get("base_model"),
        "iters": data.get("iters"),
        "final_train_loss": data.get("final_train_loss"),
        "final_val_loss": data.get("final_val_loss"),
        "error_message": data.get("error_message"),
        "session_id": data.get("session_id"),
        "iteration_number": data.get("iteration_number"),
    }


@tool
def list_experiments() -> list[dict[str, Any]]:
    """List training experiments (autoresearch sessions)."""
    data = _safe_get("/api/v1/sessions")
    if isinstance(data, dict) and "error" in data:
        return [data]
    keep = (
        "id", "name", "dataset", "base_model", "status",
        "current_round", "max_rounds", "best_run_id",
        "best_metric_value", "target_metric", "created_at",
    )
    return [{k: s.get(k) for k in keep} for s in data]


@tool
def get_export_status(run_id: int) -> dict[str, Any]:
    """Return the status of the most recent export job for a run."""
    data = _safe_get("/api/v1/exports")
    if isinstance(data, dict) and "error" in data:
        return data
    matches = [e for e in data if e.get("run_id") == run_id]
    if not matches:
        return {"run_id": run_id, "status": "none", "message": "No exports for this run."}
    latest = max(matches, key=lambda e: e.get("created_at", ""))
    return {
        "id": latest.get("id"),
        "run_id": latest.get("run_id"),
        "status": latest.get("status"),
        "progress_text": latest.get("progress_text"),
        "quant_levels": latest.get("quant_levels"),
        "error_message": latest.get("error_message"),
    }


# ─── Mutating tools (gated by confirmation) ───────────────────────


@tool
def start_experiment(
    name: str,
    dataset: str,
    base_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
    iters: int = 100,
    max_rounds: int = 8,
) -> dict[str, Any]:
    """Propose starting a new autoresearch experiment.

    IMPORTANT: This tool does NOT execute. It returns a confirmation envelope
    that the UI renders as a card with Start/Edit/Cancel. After the user
    clicks Start, the UI invokes ``confirm_start_experiment`` with the payload.
    """
    payload = {
        "name": name,
        "dataset": dataset,
        "base_model": base_model,
        "iters": iters,
        "max_rounds": max_rounds,
    }
    return {
        "requires_confirmation": True,
        "action": "start_experiment",
        "payload": payload,
        "summary": (
            f"Start experiment '{name}' on dataset '{dataset}' using "
            f"{base_model} for up to {max_rounds} rounds × {iters} iters."
        ),
    }


@tool
def confirm_start_experiment(payload: dict[str, Any]) -> dict[str, Any]:
    """Actually start an experiment after the user has confirmed.

    Invoked by the UI in response to the Confirm button on a
    ``start_experiment`` card. POSTs to /api/v1/sessions.
    """
    result = _safe_post("/api/v1/sessions", payload)
    if isinstance(result, dict) and "error" in result:
        return result
    return {
        "id": result.get("id"),
        "name": result.get("name"),
        "dataset": result.get("dataset"),
        "status": result.get("status"),
        "max_rounds": result.get("max_rounds"),
        "created_at": result.get("created_at"),
    }


# ─── Hermes / hyperparam proposal ─────────────────────────────────


@tool
def propose_hyperparams(dataset: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask Hermes for the next hyperparameter mutation to try.

    Args:
        dataset: The dataset name.
        history: List of past iteration dicts (hyperparams + metrics).
    Returns the proposal as a dict (learning_rate, batch_size, ...,
    reasoning, expected_outcome) along with a ``baseline`` snapshot for
    the UI to diff against.
    """
    try:
        from packages.ratchet.hermes_bridge import propose_mutation

        baseline_metric = None
        if history:
            for h in reversed(history):
                m = h.get("final_val_loss") or h.get("val_loss")
                if m is not None:
                    baseline_metric = m
                    break

        proposal = propose_mutation(
            dataset=dataset,
            history=history,
            current_best_metric=baseline_metric,
        )
        latest = history[-1] if history else {}
        return {
            "baseline": {
                "learning_rate": latest.get("learning_rate"),
                "batch_size": latest.get("batch_size"),
                "num_layers": latest.get("num_layers"),
                "iters": latest.get("iters"),
                "max_seq_length": latest.get("max_seq_length"),
            },
            "proposal": proposal.model_dump(),
        }
    except Exception as e:
        log.exception("propose_hyperparams failed")
        return {"error": f"{type(e).__name__}: {e}"}


# ─── Documentation search ─────────────────────────────────────────


_DOCS_ROOTS = [
    Path("/app/docs"),
    Path(__file__).resolve().parents[2] / "docs",
]


@tool
def search_docs(query: str) -> list[dict[str, Any]]:
    """Grep the project docs for ``query`` and return up to 3 snippets.

    Each snippet is {file, line, text}.
    """
    if not query or not query.strip():
        return []

    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
    results: list[dict[str, Any]] = []
    for root in _DOCS_ROOTS:
        if not root.exists():
            continue
        for md in sorted(root.rglob("*.md")):
            try:
                with md.open("r", encoding="utf-8") as f:
                    for lineno, line in enumerate(f, start=1):
                        if pattern.search(line):
                            results.append(
                                {
                                    "file": str(md),
                                    "line": lineno,
                                    "text": line.rstrip("\n")[:240],
                                }
                            )
                            if len(results) >= 3:
                                return results
            except OSError as e:
                log.warning("Could not read %s: %s", md, e)
        if results:
            break
    return results


ALL_TOOLS = [
    list_datasets,
    list_runs,
    get_run_metrics,
    get_run_status,
    list_experiments,
    start_experiment,
    confirm_start_experiment,
    propose_hyperparams,
    get_export_status,
    search_docs,
]
