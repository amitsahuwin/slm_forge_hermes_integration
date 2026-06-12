# MCP Sync Spec — bring the MCP server in line with Phases O–S

> **Status:** approved · **Date:** 2026-06-12 · **Scope:** small sync patch

## 1. Drift found (audit of `mcp_server/server.py`, 37 tools)

1. `start_experiment` defaults `base_model` to the **broken**
   `gemma-3n-E2B-it-bf16` — same stale default fixed in `RunCreate` and
   the chat agent during Phase P. MCP-driven experiments with the default
   would 422 (or ratchet into a broken model with enforcement off).
2. No tool exposes the backend-aware **catalog v2** (`/api/v1/models/v2`)
   — MCP clients can't see valid model ids, memory needs, or statuses.
3. No tool creates a **single run**, so the `trainer_backend` field
   (Phase O/R) is unreachable from MCP — Claude can't queue a CUDA job.
4. `list_runs` lacks the Phase R `backend` filter.
5. Docs say "34 tools" (README ×2, MCP_SETUP tool table) — count is stale
   even before this patch.

## 2. Changes

- `start_experiment` default → `mlx-community/Qwen2.5-3B-Instruct-4bit`.
- New tool **`list_models`** → `GET /api/v1/models/v2` (description tells
  the model how to read `backends.{mlx,cuda}.model_id/min_memory_gb/status`).
- New tool **`start_run`** → `POST /api/v1/runs` with `dataset`,
  `base_model`, `trainer_backend` (default `mlx`), `method`, `iters`,
  `batch_size`, `learning_rate` — the API's catalog validation surfaces
  naturally as the HTTP 422 error text.
- `list_runs` gains optional `backend` param.
- Docs: tool count → **39**; MCP_SETUP tool table gains the two tools;
  README's two "34 tools" mentions updated.

## 3. Acceptance criteria / tests (`tests/mcp/test_server_sync.py`)

- A1. `start_experiment`'s default equals the catalog default
  (asserted against `model_catalog.default_model_id("mlx")` — can never
  go stale again).
- A2. `list_models` GETs `/api/v1/models/v2`.
- A3. `start_run` POSTs to `/api/v1/runs` with `trainer_backend` included
  (default `mlx`, explicit `cuda` passes through).
- A4. `list_runs(backend="cuda")` forwards the query param;
  omitted → not sent.
- A5. Full suite green; ruff clean. Tests import the real server module
  (`mcp` extra installed) and monkeypatch its `_get`/`_post` plumbing —
  no network.
