# SLM-Forge MCP Server

This document explains how to expose **SLM-Forge's full capability set** to
external MCP (Model Context Protocol) clients — Claude Desktop, Cursor, and
the Claude Code CLI — so you can drive the lab from natural-language chat.

> **What is MCP?** A small, standardised protocol that lets an LLM client
> (Claude Desktop, Cursor, etc.) call tools and read resources exposed by a
> separate server process. SLM-Forge ships a thin MCP server that proxies
> every meaningful operation (datasets, runs, exports, Hermes skills, R&D
> research) to the FastAPI surface running at `http://localhost:8000`.

---

## 1. Why use it?

Without MCP you drive SLM-Forge through the React dashboard or `curl`. With
MCP you can say things like:

- *"List my datasets and tell me which one is biggest."*
- *"Run a data-quality review on `support_tickets`, then propose a canary set
  and save it."*
- *"Diagnose run #42 — was it OOM or something else?"*
- *"Start an autoresearch session on `agent_traces` with lora and 8 rounds,
  then keep an eye on canary drift."*
- *"Write me a deep research report on small LMs in 2026 and show me the
  result."*

Every tool below is just a wrapper around an existing SLM-Forge HTTP
endpoint — there is no business logic duplicated here, so the MCP surface
stays in lockstep with the API as it evolves.

---

## 2. Prerequisites

1. The SLM-Forge API is running on `http://localhost:8000` (default
   `docker compose up -d api`).
2. Python 3.12+ on your host *if* you want the **stdio** transport (Claude
   Desktop, Claude Code). The HTTP transport runs inside Docker, no host
   Python needed.
3. The `mcp` Python SDK installed in the project's virtualenv. The repo's
   `pyproject.toml` adds an `mcp` extra:

   ```bash
   uv sync --extra mcp
   ```

---

## 3. Transports

The same server supports two transports. Pick whichever your client speaks.

| Transport | Command | Use with |
|-----------|---------|----------|
| **stdio** | `python -m mcp_server` | Claude Desktop, Claude Code CLI |
| **SSE/HTTP** | `python -m mcp_server --http --port 8765` | Cursor (HTTP), in-browser inspectors, remote clients |

The Docker compose `mcp` service runs the HTTP variant.

```bash
# Optional: start the HTTP MCP server in Docker alongside the API
docker compose --profile mcp up -d
# Server is then reachable at http://localhost:8765/sse
```

---

## 4. Claude Desktop install

Edit (or create) the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Option A — stdio (recommended, no extra ports)

```json
{
  "mcpServers": {
    "slm-forge": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/slm_forge_hermes_integration",
        "run",
        "python",
        "-m",
        "mcp_server"
      ],
      "env": {
        "SLM_FORGE_API_URL": "http://localhost:8000"
      }
    }
  }
}
```

If you prefer the system Python and have `uv sync --extra mcp` already
applied, swap the `command`/`args` for:

```json
"command": "python",
"args": ["-m", "mcp_server"],
"cwd": "/absolute/path/to/slm_forge_hermes_integration"
```

### Option B — HTTP (if you already run `docker compose --profile mcp up -d`)

Claude Desktop's HTTP transport support is recent; if your version supports
it, the entry looks like:

```json
{
  "mcpServers": {
    "slm-forge": {
      "transport": { "type": "sse", "url": "http://localhost:8765/sse" }
    }
  }
}
```

After saving, restart Claude Desktop. You'll see a small hammer/plug icon
appear in the input box once the server connects — click it to see all
`slm-forge://...` tools.

---

## 5. Cursor install

Cursor reads `.cursor/mcp.json` either in your user home or in the project
root. Drop this file at `~/.cursor/mcp.json` (or `<repo>/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "slm-forge": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

Start the HTTP server first:

```bash
docker compose --profile mcp up -d
# or, on the host:
uv run python -m mcp_server --http --port 8765
```

Then reload Cursor's MCP panel (Settings -> Features -> MCP) — `slm-forge`
should turn green.

---

## 6. Claude Code CLI install

```bash
# stdio (preferred)
claude mcp add slm-forge \
  --transport stdio \
  --command "uv" \
  --args "--directory" "/absolute/path/to/slm_forge_hermes_integration" \
         "run" "python" "-m" "mcp_server" \
  --env SLM_FORGE_API_URL=http://localhost:8000

# OR, if you already have the HTTP server running:
claude mcp add slm-forge --transport sse --url http://localhost:8765/sse
```

Confirm:

```bash
claude mcp list
```

Then any `claude` session inherits the tools automatically.

---

## 7. Quick test commands

Once installed, try these prompts in your client of choice:

1. *"Use slm-forge to list every dataset I have and tell me which has the
   most train rows."*
2. *"Use slm-forge to preview five rows from `support_tickets` and summarise
   the style."*
3. *"Use slm-forge to start a research report on 'small LM tool-calling
   benchmarks 2026' at depth standard, then poll until done."*
4. *"Use slm-forge to diagnose run 42 — if it was MPS OOM, suggest a fix and
   propose new hyperparameters."*
5. *"Use slm-forge to run the experiment_recommender agent on dataset
   `agent_traces` for 'function-calling assistant' targeting mac_desktop."*

Hermes-backed tools (`diagnose_run`, `post_mortem`, the agents) can take
30s+ — that's expected on local 30B Ollama models.

---

## 8. Tool catalogue (summary)

| Group | Tools |
|-------|-------|
| Datasets | `list_datasets`, `get_dataset`, `preview_dataset_rows`, `synthesize_dataset`, `get_synthesis_status`, `ingest_dataset_from_url`, `ingest_dataset_from_file` |
| Runs & Experiments | `list_runs`, `get_run`, `get_run_metrics`, `list_models`, `start_run`, `list_experiments`, `start_experiment` |
| Exports | `list_exports`, `start_export` |
| Hermes skills | `select_method`, `diagnose_run`, `analyze_drift`, `data_quality_review`, `propose_canary_set`, `save_canary_set`, `synth_style`, `explain_anomaly`, `recommend_quants`, `model_selection`, `post_mortem`, `auto_label` |
| Multi-step agents | `run_experiment_recommender`, `run_optimization_coach`, `run_evaluation_designer`, `run_incident_responder` |
| R&D research | `start_research`, `list_research_reports`, `get_research_report` |
| System | `health`, `hermes_status`, `tail_logs`, `get_run_logs` |
| Resources | `slm-forge://datasets/{name}/README.md`, `slm-forge://runs/{id}/training.log` |

---

## 9. Environment variables

| Var | Default | Meaning |
|-----|---------|---------|
| `SLM_FORGE_API_URL` | `http://localhost:8000` | Where the FastAPI surface lives. In the Docker `mcp` service this is set to `http://slm-forge-api:8000`. |
| `SLM_FORGE_TIMEOUT` | `300` | Per-request timeout in seconds (Hermes calls can be slow). |
| `SLM_FORGE_MCP_PORT` | `8765` | Default port for `--http`. |
| `SLM_FORGE_MCP_HOST` | `0.0.0.0` | Default host for `--http`. |

---

## 10. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Client shows the server as red / disconnected | Make sure SLM-Forge API is running (`curl http://localhost:8000/api/v1/health`). |
| `SLM-Forge API 404: Dataset not found` from a tool call | Use `list_datasets` first to see the correct names. |
| Hermes tools time out | Hermes uses local Ollama; raise `SLM_FORGE_TIMEOUT` or pull a smaller model. |
| `python -m mcp_server` fails with `No module named mcp` | Run `uv sync --extra mcp` in the repo. |
| Docker `mcp` service crashes immediately | Confirm the `api` service is healthy and that `slm-forge-api` resolves on the compose network. |
