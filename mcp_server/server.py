"""SLM-Forge MCP server.

Defines a single MCP server (``slm-forge``) that wraps every meaningful
operation of the local SLM-Forge fine-tuning lab as a tool. The server is a
thin proxy: every tool call performs an HTTP request against the existing
FastAPI surface (default ``http://localhost:8000``). All business logic lives
in ``apps/api/routers/*.py`` — this file only translates MCP tool calls
into HTTP calls and surfaces the JSON (or error) back to the model.

Why a separate process?
    Claude Desktop / Cursor / Claude Code spawn MCP servers as child
    processes (stdio) or connect over HTTP. They never talk to the FastAPI
    app directly. This package gives MCP clients a stable schema that maps
    1:1 to SLM-Forge's REST endpoints.

Transports:
    * stdio  (default, used by Claude Desktop)
    * sse/http (``--http --port 8765`` — used by web clients & Cursor)

Environment:
    SLM_FORGE_API_URL   Base URL of the running SLM-Forge API.
                        Default: ``http://localhost:8000``.
    SLM_FORGE_TIMEOUT   Per-request timeout in seconds. Default: ``300``
                        (Hermes calls on 30B local models are slow).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ─── Configuration ────────────────────────────────────────────────────────

API_URL: str = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT_SECONDS: float = float(os.environ.get("SLM_FORGE_TIMEOUT", "300"))
API_V1 = f"{API_URL}/api/v1"

mcp: FastMCP = FastMCP("slm-forge")


# ─── HTTP plumbing ────────────────────────────────────────────────────────


def _client() -> httpx.Client:
    """Return a fresh httpx client. Created per-call so MCP threading is safe."""
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT_SECONDS))


def _raise_for_status(resp: httpx.Response) -> None:
    """Re-raise upstream errors with the FastAPI ``detail`` message attached.

    MCP tools that ``raise`` end up surfacing the exception text to the model,
    so we want the upstream "Dataset not found", "Run #5 has export #2 -
    delete the export first", etc. to come through verbatim.
    """
    if resp.is_success:
        return
    detail: Any
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
    except ValueError:
        detail = resp.text or resp.reason_phrase
    raise RuntimeError(f"SLM-Forge API {resp.status_code}: {detail}")


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        r = c.get(f"{API_URL}{path}", params=params)
    _raise_for_status(r)
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text


def _post(path: str, json: dict[str, Any] | None = None) -> Any:
    with _client() as c:
        r = c.post(f"{API_URL}{path}", json=json)
    _raise_for_status(r)
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text


def _post_multipart(
    path: str, files: dict[str, Any], data: dict[str, Any] | None = None
) -> Any:
    with _client() as c:
        r = c.post(f"{API_URL}{path}", files=files, data=data or {})
    _raise_for_status(r)
    try:
        return r.json()
    except ValueError:
        return r.text


# ─── Datasets ─────────────────────────────────────────────────────────────


@mcp.tool(
    name="list_datasets",
    description=(
        "List every dataset under data/datasets/ in this local SLM-Forge lab. "
        "Returns name, train/valid counts, canary presence, and a short "
        "description for each. Use this as the entry point when the user asks "
        "'what data do I have?'."
    ),
)
def list_datasets() -> list[dict[str, Any]]:
    return _get("/api/v1/datasets")


@mcp.tool(
    name="get_dataset",
    description=(
        "Return full detail for one SLM-Forge dataset — counts, length stats, "
        "README markdown, and preview rows for each split. Use after "
        "list_datasets when the user picks one to dig into."
    ),
)
def get_dataset(
    name: Annotated[str, Field(description="Dataset folder name (e.g. 'support_tickets').")],
) -> dict[str, Any]:
    return _get(f"/api/v1/datasets/{name}")


@mcp.tool(
    name="preview_dataset_rows",
    description=(
        "Return a paginated slice of rows from a dataset split. Useful for "
        "spot-checking what the trainer will actually see. SLM-Forge stores "
        "data as chat-formatted JSONL under data/datasets/<name>/<split>.jsonl."
    ),
)
def preview_dataset_rows(
    name: Annotated[str, Field(description="Dataset folder name.")],
    split: Annotated[
        str, Field(description="Which split to read: 'train', 'valid', or 'canary'.")
    ] = "train",
    offset: Annotated[int, Field(ge=0, description="Row offset (0-indexed).")] = 0,
    limit: Annotated[
        int, Field(ge=1, le=100, description="Max rows to return (1..100).")
    ] = 20,
) -> dict[str, Any]:
    return _get(
        f"/api/v1/datasets/{name}/rows",
        params={"split": split, "offset": offset, "limit": limit},
    )


@mcp.tool(
    name="synthesize_dataset",
    description=(
        "Kick off an async Ollama-driven synthesis job that expands a seed "
        "dataset to ``target_count`` records via SLM-Forge's local synthesizer. "
        "Returns immediately with a job_id — poll get_synthesis_status(job_id) "
        "to watch progress. The new dataset is written to "
        "data/datasets/<new_dataset>/ on completion."
    ),
)
def synthesize_dataset(
    source_dataset: Annotated[
        str, Field(description="Existing dataset to use as the seed pool.")
    ],
    new_dataset: Annotated[
        str, Field(description="Name of the new dataset directory to create.")
    ],
    target_count: Annotated[
        int, Field(ge=8, le=5000, description="Total records to generate (8..5000).")
    ],
    style_guidance: Annotated[
        str,
        Field(
            description=(
                "Optional natural-language guidance for the generator "
                "(e.g. 'concise, professional, no apologies')."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    return _post(
        "/api/v1/synth/start",
        json={
            "source_dataset": source_dataset,
            "new_dataset": new_dataset,
            "target_count": target_count,
            "style_guidance": style_guidance,
        },
    )


@mcp.tool(
    name="get_synthesis_status",
    description=(
        "Return the current snapshot of a synthesis job started via "
        "synthesize_dataset. Status moves through queued -> running -> "
        "completed | failed. Poll this every few seconds while running."
    ),
)
def get_synthesis_status(
    job_id: Annotated[str, Field(description="Job id returned by synthesize_dataset.")],
) -> dict[str, Any]:
    return _get(f"/api/v1/synth/jobs/{job_id}")


@mcp.tool(
    name="ingest_dataset_from_url",
    description=(
        "Fetch a URL (JSONL/CSV/PDF/JSON/etc.) and turn it into a new "
        "SLM-Forge dataset. The universal converter auto-detects the format "
        "and may optionally route through Ollama for natural-language sources."
    ),
)
def ingest_dataset_from_url(
    name: Annotated[str, Field(description="Name of the new dataset directory.")],
    url: Annotated[str, Field(description="HTTP(S) URL to fetch.")],
    force_ollama: Annotated[
        bool,
        Field(
            description=(
                "If true, force the Ollama-backed conversion path even when "
                "the file looks structured. Useful for messy text."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    return _post(
        "/api/v1/ingest/from-url",
        json={"name": name, "url": url, "force_ollama": force_ollama},
    )


@mcp.tool(
    name="ingest_dataset_from_file",
    description=(
        "Upload a local file (path is read by the MCP server process) and "
        "write it as a new SLM-Forge dataset. Supports JSONL/CSV/JSON/TXT/PDF."
    ),
)
def ingest_dataset_from_file(
    name: Annotated[str, Field(description="Name of the new dataset directory.")],
    file_path: Annotated[
        str, Field(description="Absolute path to the source file on disk.")
    ],
    force_ollama: Annotated[
        bool, Field(description="Force Ollama-backed conversion path.")
    ] = False,
) -> dict[str, Any]:
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise RuntimeError(f"File not found: {file_path}")
    with p.open("rb") as fh:
        files = {"file": (p.name, fh.read(), "application/octet-stream")}
    data = {"name": name, "force_ollama": "true" if force_ollama else "false"}
    return _post_multipart("/api/v1/ingest/file", files=files, data=data)


# ─── Runs & Experiments ───────────────────────────────────────────────────


@mcp.tool(
    name="list_runs",
    description=(
        "List recent training runs in the SLM-Forge lab. Optionally filter by "
        "status (queued/running/completed/failed/cancelled)."
    ),
)
def list_runs(
    status: Annotated[
        str | None,
        Field(
            description=(
                "Filter by run status: 'queued', 'running', 'completed', "
                "'failed', or 'cancelled'. Omit for all."
            )
        ),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max rows to return.")] = 20,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    return _get("/api/v1/runs", params=params)


@mcp.tool(
    name="get_run",
    description="Return all metadata for one SLM-Forge run by its integer id.",
)
def get_run(
    id: Annotated[int, Field(description="Numeric run id.")],
) -> dict[str, Any]:
    return _get(f"/api/v1/runs/{id}")


@mcp.tool(
    name="get_run_metrics",
    description=(
        "Return the full ordered metric time-series for a run (train loss, "
        "val loss, canary loss, learning rate, etc.). Use for plotting or "
        "feeding to explain_anomaly."
    ),
)
def get_run_metrics(
    id: Annotated[int, Field(description="Numeric run id.")],
) -> list[dict[str, Any]]:
    return _get(f"/api/v1/runs/{id}/metrics")


@mcp.tool(
    name="list_experiments",
    description=(
        "List autoresearch experiments (sessions). Each session orchestrates "
        "multiple child runs that ratchet toward the best hyperparameters."
    ),
)
def list_experiments() -> list[dict[str, Any]]:
    return _get("/api/v1/sessions")


@mcp.tool(
    name="start_experiment",
    description=(
        "Create a new autoresearch experiment (TrainingSession). The ratchet "
        "worker will pick this up and run iterations until plateau or "
        "max_rounds. Returns the created session record (includes id)."
    ),
)
def start_experiment(
    name: Annotated[str, Field(description="Human-readable session name.")],
    dataset: Annotated[
        str, Field(description="Dataset name under data/datasets/.")
    ],
    base_model: Annotated[
        str,
        Field(description="HF / mlx-community base model id."),
    ] = "mlx-community/gemma-3n-E2B-it-bf16",
    method: Annotated[
        str, Field(description="Fine-tune method: 'lora', 'dora', or 'full'.")
    ] = "lora",
    iters: Annotated[int, Field(ge=1, description="Iterations per run.")] = 100,
    max_rounds: Annotated[
        int, Field(ge=1, description="Max ratchet rounds before stopping.")
    ] = 8,
) -> dict[str, Any]:
    return _post(
        "/api/v1/sessions",
        json={
            "name": name,
            "dataset": dataset,
            "base_model": base_model,
            "method": method,
            "iters": iters,
            "max_rounds": max_rounds,
        },
    )


# ─── Exports ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="list_exports",
    description=(
        "List GGUF export jobs. Each export fuses a completed run's adapter, "
        "quantizes it, and produces .gguf files ready for llama.cpp / iPhone."
    ),
)
def list_exports() -> list[dict[str, Any]]:
    return _get("/api/v1/exports")


@mcp.tool(
    name="start_export",
    description=(
        "Start a GGUF export job for a completed run. The exporter worker "
        "fuses the adapter and quantizes to the listed levels."
    ),
)
def start_export(
    run_id: Annotated[int, Field(description="Completed run id to export.")],
    quant_levels: Annotated[
        list[str] | None,
        Field(
            description=(
                "Quant levels — any of 'Q4_K_M', 'Q5_K_M', 'Q8_0', 'F16'. "
                "Default ['Q4_K_M', 'Q5_K_M', 'Q8_0']."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"run_id": run_id}
    if quant_levels:
        payload["quant_levels"] = quant_levels
    return _post("/api/v1/exports", json=payload)


# ─── Hermes skills (one tool per skill) ──────────────────────────────────


@mcp.tool(
    name="select_method",
    description=(
        "Hermes skill: recommend a fine-tune method (lora/dora/full) and "
        "starter hyperparameters for a task. Calls the local Hermes/Ollama "
        "model via /api/v1/hermes/select-method."
    ),
)
def select_method(
    task_description: Annotated[
        str, Field(description="Plain-English description of the target behaviour.")
    ],
    base_model: Annotated[str | None, Field(description="Optional base model.")] = None,
    dataset_name: Annotated[
        str | None, Field(description="Optional dataset for context.")
    ] = None,
    n_train_examples: Annotated[
        int | None, Field(description="Optional train-row count for sizing.")
    ] = None,
) -> dict[str, Any]:
    payload = {
        "task_description": task_description,
        "base_model": base_model,
        "dataset_name": dataset_name,
        "n_train_examples": n_train_examples,
    }
    return _post(
        "/api/v1/hermes/select-method",
        json={k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool(
    name="diagnose_run",
    description=(
        "Hermes skill: diagnose a failed run, MPS-OOM focused. Sends the run "
        "config + tail of training.log + error message to the local Hermes "
        "model and returns a structured fix recommendation."
    ),
)
def diagnose_run(
    run_id: Annotated[int, Field(description="Run id to diagnose.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/diagnose-run/{run_id}")


@mcp.tool(
    name="analyze_drift",
    description=(
        "Hermes skill: analyze canary drift for an autoresearch session and "
        "suggest mitigations (typically LR / num_layers reductions)."
    ),
)
def analyze_drift(
    session_id: Annotated[int, Field(description="Session id with drift.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/analyze-drift/{session_id}")


@mcp.tool(
    name="data_quality_review",
    description=(
        "Hermes skill: review a sample from the dataset for noise, bias, "
        "schema inconsistencies, and prompt-leak risk."
    ),
)
def data_quality_review(
    dataset: Annotated[str, Field(description="Dataset name.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/data-quality/{dataset}")


@mcp.tool(
    name="propose_canary_set",
    description=(
        "Hermes skill: generate a 5-record canary set proposal for a dataset. "
        "Use save_canary_set to persist it to data/datasets/<dataset>/canary.jsonl."
    ),
)
def propose_canary_set(
    dataset: Annotated[str, Field(description="Dataset name.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/propose-canary/{dataset}")


@mcp.tool(
    name="save_canary_set",
    description=(
        "Persist a proposed canary set to data/datasets/<dataset>/canary.jsonl. "
        "Refuses to overwrite an existing canary file."
    ),
)
def save_canary_set(
    dataset: Annotated[str, Field(description="Dataset name.")],
    canary: Annotated[
        list[dict[str, Any]],
        Field(description="List of canary record dicts (chat-formatted)."),
    ],
) -> dict[str, Any]:
    return _post(
        f"/api/v1/hermes/propose-canary/{dataset}/save",
        json={"canary": canary},
    )


@mcp.tool(
    name="synth_style",
    description=(
        "Hermes skill: build a dataset-specific style-guidance string for the "
        "synthesizer. Feed the returned string into synthesize_dataset."
    ),
)
def synth_style(
    dataset: Annotated[str, Field(description="Dataset name.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/synth-style/{dataset}")


@mcp.tool(
    name="explain_anomaly",
    description=(
        "Hermes skill: inspect a run's metric time-series and explain any "
        "anomaly (loss spike, lr collapse, divergence) in plain English."
    ),
)
def explain_anomaly(
    run_id: Annotated[int, Field(description="Run id to analyze.")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/explain-anomaly/{run_id}")


@mcp.tool(
    name="recommend_quants",
    description=(
        "Hermes skill: recommend GGUF quant levels for a completed run given "
        "the target device (e.g. 'iphone_pro') and use_case (e.g. 'chat')."
    ),
)
def recommend_quants(
    run_id: Annotated[int, Field(description="Completed run id.")],
    target_device: Annotated[
        str,
        Field(
            description="Target device, e.g. 'iphone_pro', 'mac_desktop', 'edge'."
        ),
    ] = "iphone_pro",
    use_case: Annotated[
        str,
        Field(description="Use case: 'chat', 'classification', 'code', etc."),
    ] = "chat",
) -> dict[str, Any]:
    return _post(
        f"/api/v1/hermes/recommend-quants/{run_id}",
        json={"target_device": target_device, "use_case": use_case},
    )


@mcp.tool(
    name="model_selection",
    description=(
        "Hermes skill: recommend a base model for the task. Complements "
        "select_method (which picks the method + hyperparams)."
    ),
)
def model_selection(
    task_description: Annotated[
        str, Field(description="Plain-English description of the task.")
    ],
    dataset_name: Annotated[
        str | None, Field(description="Optional dataset.")
    ] = None,
    n_train_examples: Annotated[
        int | None, Field(description="Optional train-row count.")
    ] = None,
    target_device: Annotated[
        str, Field(description="Target device.")
    ] = "mac_desktop",
) -> dict[str, Any]:
    payload = {
        "task_description": task_description,
        "dataset_name": dataset_name,
        "n_train_examples": n_train_examples,
        "target_device": target_device,
    }
    return _post(
        "/api/v1/hermes/model-selection",
        json={k: v for k, v in payload.items() if v is not None},
    )


@mcp.tool(
    name="post_mortem",
    description=(
        "Hermes skill: write a full markdown post-mortem for a run (broader "
        "than diagnose_run, which is OOM-focused). Also writes a copy to "
        "runs/<id>/post_mortem.md. Returns the markdown body."
    ),
)
def post_mortem(
    run_id: Annotated[int, Field(description="Run id (typically failed).")],
) -> dict[str, Any]:
    return _post(f"/api/v1/hermes/post-mortem/{run_id}")


@mcp.tool(
    name="auto_label",
    description=(
        "Hermes skill: convert raw text into chat-style JSONL records (one "
        "per line). Useful for bootstrapping a dataset from documents."
    ),
)
def auto_label(
    text: Annotated[str, Field(description="Raw text to convert (cap ~6000 chars).")],
    domain_hint: Annotated[
        str | None,
        Field(description="Optional domain hint (e.g. 'customer support')."),
    ] = None,
) -> dict[str, Any]:
    return _post(
        "/api/v1/hermes/auto-label",
        json={"text": text, "domain_hint": domain_hint or ""},
    )


# ─── Multi-step agents ───────────────────────────────────────────────────


@mcp.tool(
    name="run_experiment_recommender",
    description=(
        "Multi-step Hermes agent: data quality review -> base-model pick -> "
        "method + hyperparam recommendation. Returns the full recommendation "
        "plus each intermediate step. Can take 30s+ on local Ollama."
    ),
)
def run_experiment_recommender(
    dataset: Annotated[str, Field(description="Dataset to plan for.")],
    task_description: Annotated[str, Field(description="What the model should do.")],
    target_device: Annotated[
        str, Field(description="Target device for deployment.")
    ] = "mac_desktop",
) -> dict[str, Any]:
    return _post(
        "/api/v1/agents/experiment_recommender/run-sync",
        json={
            "dataset": dataset,
            "task_description": task_description,
            "target_device": target_device,
        },
    )


@mcp.tool(
    name="run_optimization_coach",
    description=(
        "Multi-step Hermes agent: looks at a session's iteration history + "
        "canary drift and recommends continue / pivot / stop."
    ),
)
def run_optimization_coach(
    session_id: Annotated[int, Field(description="Autoresearch session id.")],
) -> dict[str, Any]:
    return _post(
        "/api/v1/agents/optimization_coach/run-sync",
        json={"session_id": session_id},
    )


@mcp.tool(
    name="run_evaluation_designer",
    description=(
        "Multi-step Hermes agent: builds a 5-record canary set, success "
        "criteria, and 5 benchmark questions for a dataset."
    ),
)
def run_evaluation_designer(
    dataset: Annotated[str, Field(description="Dataset name.")],
) -> dict[str, Any]:
    return _post(
        "/api/v1/agents/evaluation_designer/run-sync",
        json={"dataset": dataset},
    )


@mcp.tool(
    name="run_incident_responder",
    description=(
        "Multi-step Hermes agent: generates a markdown post-mortem for a "
        "failed run and decides if it's safe to re-queue."
    ),
)
def run_incident_responder(
    run_id: Annotated[int, Field(description="Failed run id.")],
) -> dict[str, Any]:
    return _post(
        "/api/v1/agents/incident_responder/run-sync",
        json={"run_id": run_id},
    )


# ─── R&D research ────────────────────────────────────────────────────────


@mcp.tool(
    name="start_research",
    description=(
        "Kick off an async market-research report on a topic. Depth controls "
        "section count and Ollama call budget. Returns a job_id; the final "
        "markdown is written to docs/market-research/<filename>.md."
    ),
)
def start_research(
    topic: Annotated[
        str, Field(min_length=3, max_length=500, description="Research topic.")
    ],
    depth: Annotated[
        str,
        Field(description="Depth: 'quick', 'standard', or 'deep'."),
    ] = "standard",
) -> dict[str, Any]:
    return _post("/api/v1/research/start", json={"topic": topic, "depth": depth})


@mcp.tool(
    name="list_research_reports",
    description=(
        "List previously generated R&D markdown reports under docs/market-research/."
    ),
)
def list_research_reports() -> list[dict[str, Any]]:
    return _get("/api/v1/research/reports")


@mcp.tool(
    name="get_research_report",
    description=(
        "Return the full markdown body of a research report by filename. "
        "Filename must match the entries from list_research_reports."
    ),
)
def get_research_report(
    filename: Annotated[
        str, Field(description="Report filename, e.g. 'small-lms-2026.md'.")
    ],
) -> dict[str, Any]:
    return _get(f"/api/v1/research/reports/{filename}")


# ─── System ──────────────────────────────────────────────────────────────


@mcp.tool(
    name="health",
    description=(
        "Return the SLM-Forge API health snapshot: version, uptime, and "
        "capability flags (chat installed, trainer reachable, etc.)."
    ),
)
def health() -> dict[str, Any]:
    return _get("/api/v1/health")


@mcp.tool(
    name="hermes_status",
    description=(
        "Return the Hermes/Ollama bridge status: reachability, model name, "
        "worker liveness, installed skills directory."
    ),
)
def hermes_status() -> dict[str, Any]:
    return _get("/api/v1/hermes/status")


@mcp.tool(
    name="tail_logs",
    description=(
        "Tail the last N lines of a worker's log file. Workers: 'api', "
        "'ratchet', 'trainer', 'exporter'."
    ),
)
def tail_logs(
    worker: Annotated[
        str,
        Field(description="Worker name: 'api', 'ratchet', 'trainer', 'exporter'."),
    ],
    n: Annotated[int, Field(ge=1, le=5000, description="Lines to tail.")] = 200,
) -> dict[str, Any]:
    return _get(f"/api/v1/logs/{worker}", params={"n": n})


@mcp.tool(
    name="get_run_logs",
    description=(
        "Tail the last N lines of a specific run's training.log "
        "(runs/<run_id>/training.log)."
    ),
)
def get_run_logs(
    run_id: Annotated[int, Field(description="Numeric run id.")],
    n: Annotated[int, Field(ge=1, le=5000, description="Lines to tail.")] = 500,
) -> dict[str, Any]:
    return _get(f"/api/v1/runs/{run_id}/logs", params={"n": n})


# ─── Resources ───────────────────────────────────────────────────────────
#
# MCP "resources" are read-only URIs the host can browse. SLM-Forge exposes
# two convenience resources so clients can pin dataset READMEs or training
# logs into a conversation without going through a tool call.


@mcp.resource("slm-forge://datasets/{name}/README.md")
def dataset_readme(name: str) -> str:
    """Return the README.md for a dataset."""
    detail = _get(f"/api/v1/datasets/{name}")
    if isinstance(detail, dict):
        md = detail.get("readme_markdown") or ""
        if md:
            return md
    return f"# {name}\n\n(No README found.)"


@mcp.resource("slm-forge://runs/{run_id}/training.log")
def run_training_log(run_id: str) -> str:
    """Return the tail of a run's training.log."""
    try:
        rid = int(run_id)
    except ValueError as e:
        raise RuntimeError(f"Invalid run_id: {run_id!r}") from e
    data = _get(f"/api/v1/runs/{rid}/logs", params={"n": 500})
    if isinstance(data, dict):
        return "\n".join(data.get("lines", []))
    return str(data)


# ─── Transport runners ───────────────────────────────────────────────────


def run_stdio() -> None:
    """Run the MCP server over stdio (default for Claude Desktop)."""
    mcp.run(transport="stdio")


def run_http(port: int = 8765, host: str = "0.0.0.0") -> None:
    """Run the MCP server over SSE/HTTP.

    Uses FastMCP's built-in ``sse`` transport (uvicorn under the hood). The
    port + host can be overridden when calling.
    """
    # FastMCP exposes settings via attributes on the instance.
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="sse")


__all__ = ["mcp", "run_stdio", "run_http", "API_URL", "TIMEOUT_SECONDS"]
