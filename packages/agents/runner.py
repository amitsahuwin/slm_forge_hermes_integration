"""Multi-step Hermes agents (Phase N.3).

Each agent here is a small LangGraph state graph that chains 2-4 existing
Hermes skills into a single workflow. The graph emits ``stage`` progress
events at every node transition so the UI can stream live updates.

Four agents:
  • experiment_recommender — quality_review → model_selection → propose_hyperparams
  • optimization_coach     — analyze_drift → propose_hyperparams (decision: continue/pivot/stop)
  • evaluation_designer    — quality_review → propose_canary_set + benchmark + criteria
  • incident_responder     — failure_post_mortem on the most recent failed run

The agents are deliberately small (3-4 nodes each) — multi-step reasoning
is the point but we want each step inspectable. They share a single state
schema and a single Ollama bridge.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from packages.ratchet.hermes_bridge import (
    HERMES_MODEL,
    OLLAMA_URL,
    _call_ollama,
    load_skill,
)

log = logging.getLogger("agents.runner")

# ─── Shared state ────────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    # Inputs (filled by caller)
    inputs: dict[str, Any]
    # Per-stage results, keyed by skill/node name
    steps: dict[str, dict[str, Any]]
    # Final, synthesized recommendation produced by the last node
    recommendation: dict[str, Any]
    # Free-form log of what happened (one line per step)
    log: list[str]


AgentEvent = dict[str, Any]


# ─── Skill invocation helper (reused across all agents) ──────────────────


def _call_skill(skill_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call one Hermes skill and return ``{parsed, raw}``.

    A failure returns ``{"parsed": None, "raw": "", "error": str(e)}`` rather
    than raising so the agent can decide whether to continue or abort.
    """
    skill = load_skill(skill_name)
    if skill is None:
        return {
            "parsed": None,
            "raw": "",
            "error": f"skill {skill_name!r} not installed",
        }
    try:
        raw = _call_ollama(skill, json.dumps(payload, default=str), expect_json=True)
    except Exception as e:
        log.exception("skill %s call failed", skill_name)
        return {"parsed": None, "raw": "", "error": f"{type(e).__name__}: {e}"}
    parsed: dict[str, Any] | None = None
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            parsed = d
    except json.JSONDecodeError:
        pass
    return {"parsed": parsed, "raw": raw, "error": None}


# ─── Dataset / run loaders (lift the same logic the API router uses) ─────


def _load_dataset_sample(dataset: str, n: int = 8) -> tuple[str, list[dict[str, Any]]]:
    from pathlib import Path as _P

    cands = [
        _P("/app/data/datasets") / dataset,
        _P(__file__).resolve().parents[2] / "data" / "datasets" / dataset,
    ]
    ds_dir = next((c for c in cands if c.exists()), None)
    if ds_dir is None:
        return "", []
    train = ds_dir / "train.jsonl"
    if not train.exists():
        return "", []
    rows: list[dict[str, Any]] = []
    with train.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except json.JSONDecodeError:
                continue
    desc = ""
    readme = ds_dir / "README.md"
    if readme.exists():
        for raw in readme.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                desc = s
                break
    return desc, rows


# ─── Agent 1: experiment_recommender ─────────────────────────────────────
#
# Chain:
#   1. data_quality_review        (decide if dataset is fit for fine-tuning)
#   2. model_selection            (recommend base model)
#   3. select_method_for_task     (recommend method + baseline hyperparams)
# Final node composes a "Recommended experiment plan" object.


def run_experiment_recommender(
    dataset: str,
    task_description: str,
    target_device: str = "mac_desktop",
    progress: list[AgentEvent] | None = None,
) -> AgentState:
    state: AgentState = {
        "inputs": {
            "dataset": dataset,
            "task_description": task_description,
            "target_device": target_device,
        },
        "steps": {},
        "log": [],
    }

    def _emit(stage: str, **kw: Any) -> None:
        ev = {"stage": stage, "ts": datetime.now(UTC).isoformat(), **kw}
        if progress is not None:
            progress.append(ev)

    desc, sample = _load_dataset_sample(dataset, n=8)
    if not sample:
        state["log"].append(f"dataset {dataset!r} not found — using stub")
    state["inputs"]["dataset_description"] = desc
    state["inputs"]["n_train_examples"] = len(sample)

    # Step 1: quality review
    _emit("quality_review_start")
    qr = _call_skill(
        "data_quality_review",
        {"dataset": dataset, "description": desc, "sample": sample},
    )
    state["steps"]["data_quality_review"] = qr
    state["log"].append(
        f"quality: {(qr['parsed'] or {}).get('overall_health', 'unknown')}"
    )
    _emit("quality_review_done", result=qr)

    # Step 2: model selection
    _emit("model_selection_start")
    ms = _call_skill(
        "model_selection",
        {
            "task_description": task_description,
            "dataset_name": dataset,
            "n_train_examples": len(sample),
            "target_device": target_device,
        },
    )
    state["steps"]["model_selection"] = ms
    state["log"].append(
        f"model: {(ms['parsed'] or {}).get('primary', 'unknown')}"
    )
    _emit("model_selection_done", result=ms)

    # Step 3: method + hyperparams
    primary_model = (ms["parsed"] or {}).get("primary", "mlx-community/Qwen2.5-3B-Instruct-4bit")
    _emit("method_selection_start")
    mp = _call_skill(
        "select_method_for_task",
        {
            "task_description": task_description,
            "base_model": primary_model,
            "dataset_name": dataset,
            "n_train_examples": len(sample),
        },
    )
    state["steps"]["select_method_for_task"] = mp
    state["log"].append(
        f"method: {(mp['parsed'] or {}).get('method', 'unknown')}"
    )
    _emit("method_selection_done", result=mp)

    # Final synthesis
    quality = qr.get("parsed") or {}
    model_pick = ms.get("parsed") or {}
    method_pick = mp.get("parsed") or {}
    state["recommendation"] = {
        "ready_to_train": quality.get("ready_to_train", True),
        "data_quality_summary": quality.get("summary"),
        "high_severity_issues": [
            i for i in (quality.get("issues") or []) if i.get("severity") == "high"
        ],
        "base_model": model_pick.get("primary"),
        "model_alternatives": model_pick.get("alternatives", []),
        "method": method_pick.get("method"),
        "num_layers": method_pick.get("num_layers"),
        "learning_rate": method_pick.get("learning_rate"),
        "batch_size": method_pick.get("batch_size"),
        "iters": method_pick.get("iters"),
        "rationale": method_pick.get("reasoning"),
    }
    _emit("done", recommendation=state["recommendation"])
    return state


# ─── Agent 2: optimization_coach ─────────────────────────────────────────


def run_optimization_coach(
    session_id: int,
    iterations: list[dict[str, Any]],
    drift_threshold: float,
    dataset: str,
    progress: list[AgentEvent] | None = None,
) -> AgentState:
    state: AgentState = {
        "inputs": {
            "session_id": session_id,
            "drift_threshold": drift_threshold,
            "dataset": dataset,
            "iteration_count": len(iterations),
        },
        "steps": {},
        "log": [],
    }

    def _emit(stage: str, **kw: Any) -> None:
        ev = {"stage": stage, "ts": datetime.now(UTC).isoformat(), **kw}
        if progress is not None:
            progress.append(ev)

    drifts = [
        abs((it.get("canary_loss") or 0) - (it.get("val_loss") or 0))
        for it in iterations
        if it.get("canary_loss") is not None and it.get("val_loss") is not None
    ]
    max_drift = max(drifts) if drifts else None

    # Step 1: drift analysis
    _emit("drift_analysis_start")
    da = _call_skill(
        "analyze_canary_drift",
        {
            "session_id": session_id,
            "dataset": dataset,
            "drift_threshold": drift_threshold,
            "max_observed_drift": max_drift,
            "iterations": iterations,
        },
    )
    state["steps"]["analyze_canary_drift"] = da
    _emit("drift_analysis_done", result=da)

    # Step 2: next mutation proposal (reuse propose_hyperparam_mutation skill)
    _emit("mutation_proposal_start")
    mp = _call_skill(
        "propose_hyperparam_mutation",
        {
            "dataset": dataset,
            "history": iterations,
            "current_best_metric": min(
                (it.get("val_loss") for it in iterations if it.get("val_loss") is not None),
                default=None,
            ),
        },
    )
    state["steps"]["propose_hyperparam_mutation"] = mp
    _emit("mutation_proposal_done", result=mp)

    # Final synthesis — continue / pivot / stop heuristic
    decision: Literal["continue", "pivot", "stop"] = "continue"
    reason = "Healthy iteration history; keep going."
    if max_drift is not None and max_drift > drift_threshold * 2:
        decision = "stop"
        reason = (
            f"Canary drift {max_drift:.2f} is more than 2× threshold "
            f"{drift_threshold:.2f}. Likely serious overfitting; roll back."
        )
    elif max_drift is not None and max_drift > drift_threshold:
        decision = "pivot"
        reason = (
            f"Canary drift {max_drift:.2f} above threshold {drift_threshold:.2f}. "
            "Reduce LR + num_layers per the drift analysis before continuing."
        )
    elif len(iterations) >= 5:
        # Look at val_loss trend over the last 3 iterations
        recent = [
            it.get("val_loss")
            for it in iterations[-3:]
            if it.get("val_loss") is not None
        ]
        if len(recent) >= 3 and recent[-1] >= recent[0] - 0.005:
            decision = "stop"
            reason = "Val loss has plateaued over the last 3 iterations."

    state["recommendation"] = {
        "decision": decision,
        "reason": reason,
        "max_observed_drift": max_drift,
        "drift_threshold": drift_threshold,
        "drift_analysis": da.get("parsed"),
        "next_mutation": mp.get("parsed"),
    }
    _emit("done", recommendation=state["recommendation"])
    return state


# ─── Agent 3: evaluation_designer ────────────────────────────────────────


def run_evaluation_designer(
    dataset: str,
    progress: list[AgentEvent] | None = None,
) -> AgentState:
    state: AgentState = {
        "inputs": {"dataset": dataset},
        "steps": {},
        "log": [],
    }

    def _emit(stage: str, **kw: Any) -> None:
        ev = {"stage": stage, "ts": datetime.now(UTC).isoformat(), **kw}
        if progress is not None:
            progress.append(ev)

    desc, sample = _load_dataset_sample(dataset, n=10)
    if not sample:
        state["log"].append(f"dataset {dataset!r} not found")
        return state

    # Step 1: quality review (to identify weak spots that canary should test)
    _emit("quality_review_start")
    qr = _call_skill(
        "data_quality_review",
        {"dataset": dataset, "description": desc, "sample": sample},
    )
    state["steps"]["data_quality_review"] = qr
    _emit("quality_review_done", result=qr)

    # Step 2: canary set proposal
    _emit("canary_proposal_start")
    cs = _call_skill(
        "propose_canary_set",
        {"dataset": dataset, "description": desc, "sample": sample},
    )
    state["steps"]["propose_canary_set"] = cs
    _emit("canary_proposal_done", result=cs)

    # Step 3: synthesize benchmark + success criteria (free-form)
    skill = load_skill("data_quality_review") or ""
    crit_prompt = json.dumps(
        {
            "dataset": dataset,
            "description": desc,
            "quality_summary": (qr.get("parsed") or {}).get("summary"),
            "instruction": (
                "Given the dataset description, propose 3 success criteria "
                "(specific, measurable, threshold-based) and 5 benchmark "
                "questions a fine-tuned model should answer well. Output JSON "
                '{"success_criteria": [{"name":..., "metric":..., "threshold":...}, ...], '
                '"benchmark_questions": ["...", ...]}'
            ),
        },
        default=str,
    )
    _emit("criteria_start")
    try:
        raw = _call_ollama(
            "You are an ML evaluation designer. Output JSON only. "
            + (skill[:400] if skill else ""),
            crit_prompt,
            expect_json=True,
        )
        try:
            crit = json.loads(raw)
        except json.JSONDecodeError:
            crit = {"raw": raw}
    except Exception as e:
        crit = {"error": f"{type(e).__name__}: {e}"}
    state["steps"]["criteria"] = crit
    _emit("criteria_done", result=crit)

    state["recommendation"] = {
        "canary_set": (cs.get("parsed") or {}).get("canary", []),
        "canary_rationale": (cs.get("parsed") or {}).get("rationale", []),
        "success_criteria": (crit.get("success_criteria") if isinstance(crit, dict) else None),
        "benchmark_questions": (crit.get("benchmark_questions") if isinstance(crit, dict) else None),
        "quality_health": (qr.get("parsed") or {}).get("overall_health"),
    }
    _emit("done", recommendation=state["recommendation"])
    return state


# ─── Agent 4: incident_responder ─────────────────────────────────────────


def run_incident_responder(
    run_id: int,
    run_data: dict[str, Any],
    training_log_tail: str,
    progress: list[AgentEvent] | None = None,
) -> AgentState:
    """Auto-fire when a worker goes stale OR a run fails.

    Produces a post-mortem markdown + decides if the run is safe to re-queue.
    Lightweight: one skill call wrapped in agent-state envelope so future
    additions (auto-requeue, notify) plug in cleanly.
    """
    state: AgentState = {
        "inputs": {"run_id": run_id},
        "steps": {},
        "log": [],
    }

    def _emit(stage: str, **kw: Any) -> None:
        ev = {"stage": stage, "ts": datetime.now(UTC).isoformat(), **kw}
        if progress is not None:
            progress.append(ev)

    _emit("post_mortem_start")
    pm = _call_skill(
        "failure_post_mortem",
        {**run_data, "run_id": run_id, "training_log_tail": training_log_tail},
    )
    state["steps"]["failure_post_mortem"] = pm
    _emit("post_mortem_done", result=pm)

    # Extract the trailing JSON block from the markdown body, if present.
    rerun_safe = False
    root_cause = "unknown"
    if pm.get("raw"):
        body = pm["raw"]
        if "```json" in body:
            try:
                tail = body.split("```json", 1)[1].split("```", 1)[0]
                meta = json.loads(tail.strip())
                rerun_safe = bool(meta.get("rerun_safe", False))
                root_cause = str(meta.get("root_cause", root_cause))
            except (json.JSONDecodeError, IndexError):
                pass

    state["recommendation"] = {
        "post_mortem_markdown": pm.get("raw", ""),
        "root_cause": root_cause,
        "rerun_safe": rerun_safe,
        "needs_human_attention": not rerun_safe,
    }
    _emit("done", recommendation=state["recommendation"])
    return state


# ─── Async streaming wrapper (used by the SSE router) ────────────────────


async def stream_agent(
    name: str,
    *args: Any,
    **kwargs: Any,
) -> AsyncGenerator[AgentEvent, None]:
    """Run an agent in a worker thread and yield events as they're produced.

    Phase B — wraps the whole run in a ``trace_span(kind='agent')`` so the
    Traces tab shows a top-level row for the agent invocation with each
    Hermes skill call nested beneath it. Because the actual work runs in
    a worker thread (via ``loop.run_in_executor``), we capture the
    current contextvars context with ``contextvars.copy_context()`` and
    have the executor *run inside* that copy. Without this hop, the
    threadpool would start with an empty context and child skill traces
    would not inherit the agent's ``trace_id``.
    """
    import asyncio
    import contextvars
    import uuid as _uuid

    runners = {
        "experiment_recommender": run_experiment_recommender,
        "optimization_coach": run_optimization_coach,
        "evaluation_designer": run_evaluation_designer,
        "incident_responder": run_incident_responder,
    }
    if name not in runners:
        yield {"stage": "error", "message": f"unknown agent {name!r}"}
        return

    # Lazy import: avoids pulling SQLModel into worker-side imports of
    # this module (workers may not have the API installed).
    from apps.api.services.tracing import trace_span

    agent_run_id = _uuid.uuid4().hex[:16]
    progress: list[AgentEvent] = []
    loop = asyncio.get_event_loop()

    with trace_span(kind="agent", name=name, agent_run_id=agent_run_id) as agent_span:

        async def runner() -> AgentState | Exception:
            try:
                ctx = contextvars.copy_context()
                return await loop.run_in_executor(
                    None,
                    lambda: ctx.run(runners[name], *args, progress=progress, **kwargs),
                )
            except Exception as e:
                return e

        task = asyncio.create_task(runner())
        seen = 0
        while not task.done():
            await asyncio.sleep(0.2)
            while seen < len(progress):
                yield progress[seen]
                seen += 1
        while seen < len(progress):
            yield progress[seen]
            seen += 1

        result = await task
        if isinstance(result, Exception):
            yield {"stage": "error", "message": f"{type(result).__name__}: {result}"}
            return

        recommendation = result.get("recommendation", {}) if isinstance(result, dict) else {}
        agent_span.set_result({"agent": name, "recommendation": recommendation})
        yield {
            "stage": "complete",
            "agent": name,
            "agent_run_id": agent_run_id,
            "trace_id": agent_span.trace_id,
            "recommendation": recommendation,
            "steps": result.get("steps", {}) if isinstance(result, dict) else {},
        }


__all__ = [
    "HERMES_MODEL",
    "OLLAMA_URL",
    "AgentEvent",
    "AgentState",
    "run_evaluation_designer",
    "run_experiment_recommender",
    "run_incident_responder",
    "run_optimization_coach",
    "stream_agent",
]
