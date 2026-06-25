# slm_forge_hermes_integration — Source Map & Invariants

Local-first SLM fine-tuning lab. FastAPI + SQLite backend and React UI in Docker; GPU workers (trainer / ratchet / exporter) run on the **host**. Multi-backend training: MLX on Apple Silicon, PEFT+TRL on NVIDIA CUDA.

## Top-level layout
- `apps/api/` — FastAPI + SQLModel + sse-starlette backend (Docker, port 8000).
- `apps/web/` — React 19 + Vite + Tailwind + react-router 7 (Docker, port 5173). See `mem:web/core`.
- `packages/` — Host workers + agents. Trainer/Ratchet/Exporter run with GPU/Metal access. See `mem:trainer/core`, `mem:ratchet/core`.
- `mcp_server/` — MCP server exposing the lab to Claude Desktop/Cursor/Claude Code (`make mcp-up`, port 8765).
- `policies/` — OPA Rego policies (AAA, role-based).
- `tests/` — pytest tree mirroring `apps/`+`packages/`.
- `docs/specs/` — phase specs (note: `docs/specs/` not `docs/spec/` per repo convention). `docs/plans/` — phased build log.
- `release/` — Keep-a-Changelog notes per release.
- `scripts/` — install, seed, smoke, llama.cpp helpers.
- `graphify-out/` — knowledge graph (`graph.json`, `wiki/`, `GRAPH_REPORT.md`) — **always query via `graphify` CLI before reading source for orientation**. See `mem:graphify`.
- `.hermes-skills/` — skills shipped to `~/.hermes/skills/`.

## Core invariants
- **Run vs. Session**: `Run` (`apps/api/models/run.py`) = one fine-tune job. `TrainingSession` (`apps/api/models/session.py`) = autoresearch experiment (sequence of Runs). Session-level fields (e.g. `base_model`, `trainer_backend`) must be threaded onto each child Run in the ratchet loop's `run_payload` or runs inherit model defaults.
- **Backend-aware claim queue**: Workers `POST /api/v1/runs/claim` filtered by `trainer_backend` (`"mlx"|"cuda"`) — atomic compare-and-swap + lease recovery. **No shared filesystem**: datasets download / adapters upload over HTTP. A run queued for a backend with no live worker stays `queued` forever.
- **Pluggable trainer**: `packages/trainer/backends/` registers backends behind `TrainerBackend` (`base.py`). `runner.py` runs the subprocess and parses stdout into normalized `TrainEvent`s posted as metrics. Workers inherit `os.environ` into subprocesses; entrypoints load `.env` (e.g. `HF_TOKEN`).
- **Model catalog**: `apps/api/services/model_catalog.py` maps one logical model → per-backend physical checkpoints (MLX 4-bit vs full-precision CUDA) with memory/status/`gated` metadata. `validate_run_request(base_model, trainer_backend)` enforces at Run *and* Session creation (422 on bad/broken/mismatched combo). Bypass: `SLM_FORGE_ENFORCE_CATALOG=false`. Frontend drives dropdowns off `/api/v1/models/v2` filtered by backend.
- **Migrations**: SQLModel `create_all` builds initial schema. Additive forward-migrations live in `apps/api/services/db.py` (`_RUN_MIGRATIONS`, `_SESSION_MIGRATIONS` → `init_db()`). Add a column there with a default rather than hand-editing tables.
- **Auth (Phase M)**: Keycloak (JWT/SSO) + OPA (Rego); service-token bypass for host workers; **off by default** (`make auth ENABLED=true|false`).
- **Observability**: `SLM_FORGE_LOG_FORMAT=json` worker logs → Promtail → Loki → Grafana. Prometheus `/metrics`. `make obs-up`.
- **Commit flow (this repo)**: commit message written to `commit_message.md` first (gitignored), then `git add . && git commit -F commit_message.md`. See `mem:conventions`.

## Memory map
- `mem:tech_stack` — languages, frameworks, key pins.
- `mem:suggested_commands` — Makefile / uv / npm / curl reference.
- `mem:conventions` — codebase rules + Definition of Done gate.
- `mem:task_completion` — what to run before declaring done.
- `mem:api/core` — FastAPI app structure (routers, models, services).
- `mem:web/core` — React/Vite frontend structure.
- `mem:trainer/core` — trainer backends + runner.
- `mem:ratchet/core` — autoresearch loop + Hermes bridge.
- `mem:graphify` — when/how to use graphify before reading source.
