# Commands

`make help` lists every target; `make platform-info` shows detected host + chosen backend.

## Setup
- `make setup` — installs uv-managed Python 3.12, `uv sync --all-extras`, `cd apps/web && npm install`. Auto-detects mac/MLX vs linux/CUDA.
- `make seed-data` — copies sample datasets into `data/datasets/`.
- `make install-hermes` / `make hermes-install-skills` — Ollama + Hermes Agent setup.

## Core stack (Docker)
- `make dev` — UI on `:5173`, API on `:8000` (foreground). `make dev-d` detached.
- `make rebuild` — `down` + `build --no-cache`.
- `make down`, `make build`, `make logs`, `make ps`.

## Host workers (run NOT in Docker — need GPU/Metal — each in its own terminal)
- `make trainer` — auto-detected backend; or `make trainer-mlx` / `make trainer-cuda`.
- `make ratchet` — autoresearch loop (requires Ollama on `:11434`).
- `make exporter` — GGUF export (requires llama.cpp tooling — `make check-llamacpp`).
- `make smoke-model MODEL=gemma-4-e4b-it ITERS=30` — smoke-test a catalog model.

## Tests / lint / types
- Full suite: `uv run pytest -q`.
- One file: `uv run pytest tests/api/test_run_validation.py`.
- One test: `uv run pytest tests/api/test_run_validation.py::test_broken_model_is_422`.
- Lint (changed files only — repo has many pre-existing findings): `uv run ruff check --fix <paths>`.
- Types: `uv run mypy apps packages`.

## Frontend
- `cd apps/web && npm run build` — `tsc --noEmit && vite build` (this is the type gate).
- `cd apps/web && npm run dev` — Vite dev server.
- `cd apps/web && npm run typecheck` — `tsc --noEmit` only.

## Auth (Keycloak + OPA)
- `make auth ENABLED=true|false` — bring up Keycloak+OPA, flip enforcement.
- `make auth-down` — tear down auth stack (core stays up).
- `make auth-token` — mint a JWT for `admin@local` (for curl testing).
- `make opa-test` — Rego unit tests (`policies/`). Uses local `opa` if available, falls back to docker.

## Observability
- `make obs-up` — Grafana `:3001` (admin/admin), Prometheus `:9090`, Loki `:3100`.
- `make obs-down`, `make obs-logs`, `make grafana`, `make prometheus`, `make loki-explore`.

## MCP server (Claude Desktop / Cursor / Claude Code)
- `make mcp-up` — HTTP transport on `:8765` (see `docs/MCP_SETUP.md`).
- `make mcp-down`, `make mcp-logs`.

## LiteLLM (Anthropic-shaped → host Ollama)
- `make litellm-up` (port 4000, autofix profile) / `make litellm-down` / `make litellm-logs`.

## Cleanup
- `make clean` — `.venv`, `node_modules`, `dist`, caches.
- `make nuke` — clean + all stacks down + volumes wiped.

## Graphify (codebase Q&A — see `mem:graphify`)
- `graphify query "<question>"` — scoped subgraph.
- `graphify explain "<concept>"` — focused concept.
- `graphify path "<A>" "<B>"` — relationships.
- `graphify update .` — refresh after code changes (AST-only, no API cost).

## Darwin-specific
- macOS shell util notes: `find -type d -name X -exec rm -rf {} +` is used in `make clean`. BSD `find` flags only. `open` is used for opening URLs in browser targets.

## NEVER do
- `pip` / direct `python` (always `uv run …`).
- `npm install` outside `apps/web/`.
- Commit `commit_message.md` (gitignored — written first, then `git commit -F`).
