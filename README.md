# SLM-Forge

> Local-first fine-tuning lab for small language models on Apple Silicon. Hermes-driven autoresearch, Ollama-powered dataset synthesis, market-research R&D, multi-step agents, structured observability, production-grade auth, and one-click GGUF export to iPhone.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Built for Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-MLX-orange.svg)](https://github.com/ml-explore/mlx)

---

## What this is

A complete pipeline for fine-tuning small language models (Qwen 2.5, Llama 3.2, Gemma 3 / Gemma 4, Mistral 7B) on your MacBook Pro, with a Hermes-agent-driven autoresearch loop that automatically explores hyperparameters, a one-click GGUF export so you can run your fine-tuned model on your iPhone offline, **plus** everything you need to run it like a real product: structured logs, Prometheus metrics, Loki + Grafana dashboards, Keycloak SSO, OPA fine-grained policy, and an MCP server that exposes the whole lab to Claude Desktop / Cursor / Claude Code.

Training is **multi-backend**: MLX on Apple Silicon (default) or PEFT + TRL on NVIDIA CUDA machines — remote GPU workers claim jobs from the same queue over HTTP, no shared filesystem needed.

Built for M3 Max with 36 GB unified memory. Smaller Apple Silicon Macs work with reduced model sizes.

## What it does

| Capability | Status |
|---|---|
| LoRA / DoRA / full SFT — MLX (Apple Silicon) or PEFT + TRL (NVIDIA CUDA) | ✓ |
| Backend-aware model catalog (Qwen 2.5 · Llama 3.2 · Gemma 3 / 4 · Mistral 7B) with memory hints + validation | ✓ |
| Remote GPU workers: atomic run claiming, lease recovery, HTTP dataset/adapter transfer | ✓ |
| Autoresearch ratchet (Hermes-driven hyperparameter sweeps) | ✓ |
| Live training metrics + ratchet timeline graphs + canary drift chart | ✓ |
| 4-source dataset ingest (file / URL / web scrape / S3) + Ollama auto-convert | ✓ |
| Ollama-driven dataset synthesis (expand 20 examples → 500+) | ✓ |
| GGUF export with Q4_K_M / Q5_K_M / Q8_0 / F16 quants | ✓ |
| LangGraph chat UI with structured tool-call cards | ✓ |
| 13 Hermes skill endpoints + 4 multi-step agents | ✓ |
| R&D market-research engine (Ollama + DDGS web grounding) | ✓ |
| Structured JSON logging + Prometheus + Loki + Grafana | ✓ |
| Keycloak (SSO) + OPA (fine-grained policy) with 6 roles | ✓ |
| MCP server (Claude Desktop / Cursor / Claude Code) | ✓ |
| Maintenance UI (disk usage, cleanup) | ✓ |
| 6 starter datasets + tool-calling guide for fine-tuned GGUFs | ✓ |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          macOS Host (M3 Max)                                │
│                                                                             │
│   ┌──────────┐    ┌──────────┐   Phase L: observability overlay             │
│   │ React UI │───▶│ FastAPI  │ ──► /metrics ──► Prometheus ──► Grafana      │
│   └──────────┘    └────┬─────┘            ┌─► Promtail ──► Loki ──► Grafana │
│                        │ SQLite + Huey    │                                 │
│                        │                  │   Phase M: AAA                  │
│                        │                  │   JWT ◀──── Keycloak (SSO)      │
│                        │                  │   policy ◀── OPA  (Rego rules)  │
│                        │                  │                                 │
│   ┌────────────────────▼──────────────────┴─────────────────────┐           │
│   │  Trainer   │  Ratchet   │  Exporter   │  Hermes Bridge      │ ← workers │
│   │  (MLX-LM)  │  (loop)    │  (GGUF)     │  + 4 agents         │   (host)  │
│   └────────────┴────────────┴────────────┴──────────┬──────────┘            │
│                                                     │                       │
│   ┌─────────────────────────────────────────────────▼────────┐              │
│   │ Ollama : qwen3:30b-a3b (skills + chat + R&D grounding)   │              │
│   └──────────────────────────────────────────────────────────┘              │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────┐              │
│   │ MCP server (8765) ── stdio + HTTP/SSE                    │              │
│   │   39 tools: datasets, runs, exports, hermes, agents…     │──────────────┼──► Claude Desktop / Cursor /
│   └──────────────────────────────────────────────────────────┘              │    Claude Code CLI
│                                                                             │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │ GGUF transfer
                             ▼
                       ┌─────────────┐
                       │   iPhone    │
                       │ PocketPal AI│
                       └─────────────┘
```

See `docs/PLAN.md` for the full phase-by-phase build log.

## Requirements

SLM-Forge runs on two host families — the **same `make` targets auto-detect
the platform** and select the right training backend (Phase T):

| | macOS (Apple Silicon → MLX) | Linux (NVIDIA → CUDA) |
|---|---|---|
| Hardware | M1 / M2 / M3 (M3 Max 36 GB is the dev target) | x86_64 + NVIDIA GPU (Tesla T4 / A100 …) |
| Python | 3.12+ — `uv` provisions a managed 3.12 even if system Python is older | same (`uv python install 3.12`) |
| Node.js | 20+ (`brew install node`) | 20+ (`apt-get install nodejs npm`) |
| GGUF tooling | `brew install llama.cpp` | build the bundled clone or install via apt/conda |
| `uv` | `brew install uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker | Docker Desktop | Docker Engine + (for GPU) NVIDIA Container Toolkit |
| Disk | ~30 GB for models + exports | ~30 GB |

`make platform-info` prints what was detected and which backend it will use.

## Quick start

```bash
# 1. Clone + one-time setup (auto-detects macOS/MLX vs Linux/CUDA)
git clone git@github.com:<you>/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration
make setup                    # uv (+ managed Python 3.12) + Node deps
make install-hermes           # Ollama + qwen3:30b-a3b (brew on macOS, systemd on Linux)
make check-llamacpp           # verify GGUF tooling (brew install llama.cpp / source build)

# 2. Start the core stack (UI on :5173, API on :8000)
make dev                      # foreground; use `make dev-d` for detached

# 3. Start the three host workers — each in its own terminal
make trainer                  # T1 — auto-selects mlx (Mac) or cuda (Linux/NVIDIA)
make ratchet                  # T2
make exporter                 # T3

# 4. Open the UI
open http://localhost:5173    # or: xdg-open on Linux
```

> Force a backend explicitly with `make trainer TRAINER_BACKEND=mlx|cuda` (or set
> `SLM_FORGE_TRAINER_BACKEND`). On a Linux box without a detected NVIDIA GPU the
> default is still `cuda`, which will report a missing toolchain until you
> `uv sync --extra trainer-cuda` on a real GPU host.

> Workers emit structured JSON logs (Phase L) by default — `runs/_<worker>.log.json` is ready for Loki to consume. Set `SLM_FORGE_LOG_FORMAT=text` in `.env` for human-readable terminal logs (at the cost of breaking the log labels in Grafana).

## End-to-end walkthrough

```
1. Ingest a dataset (file / URL / scrape / S3 / Ollama auto-convert)  → /datasets/new
2. Synthesize more examples from a small dataset via Ollama          → /datasets → "Synthesize"
3. Start an autoresearch experiment                                  → /experiments/new (or /agents → experiment_recommender)
4. Watch the ratchet graph + canary drift chart                      → /experiments/:id
5. Diagnose any failures with Hermes                                 → /runs/:id → "Post-mortem"
6. Export the winner to GGUF                                         → /runs/:id → "Export to GGUF"
7. Download Q4_K_M.gguf                                              → /exports
8. AirDrop to iPhone                                                 → PocketPal AI → Add Local Model
```

The `/chat` page exposes a categorized template library covering every step above; the `/research` page runs Ollama-driven market-research reports grounded in DuckDuckGo / SerpAPI / Tavily search.

## Multi-backend training (MLX + CUDA)

Every run targets a **training backend**, picked on the New Run form. `mlx` (default) trains on this Mac; `cuda` trains on any NVIDIA GPU machine pointed at the same API.

```bash
# Mac (default) — nothing new, same as always
make trainer

# Smoke-test a new model on this Mac (30-iter LoRA, reports peak memory)
make smoke-model MODEL=gemma-4-e4b-it

# Remote CUDA worker (Linux + NVIDIA), no shared filesystem required
docker build -f Dockerfile.trainer-cuda -t slm-forge-trainer-cuda .
docker run --gpus all \
  -e SLM_FORGE_API_URL=http://<your-mac>:8000 \
  -e SLM_FORGE_SERVICE_TOKEN=<token from .env> \
  -e HF_TOKEN=<your HF token> \
  slm-forge-trainer-cuda
```

How it works: workers **claim** runs atomically (`POST /runs/claim`) filtered by their backend, datasets download and adapters upload over HTTP, and abandoned runs are re-queued automatically after a lease timeout. The model catalog (`/api/v1/models/v2`) maps each model to its MLX 4-bit and full-precision CUDA checkpoints with memory requirements; invalid model/backend combos are rejected at run creation.

Gated models (Gemma / Llama / Mistral) need a one-time `huggingface-cli login` on the Mac, or `HF_TOKEN` on CUDA workers.

Full design + hardware feasibility tables: [`docs/MULTI_PLATFORM_TRAINING.md`](docs/MULTI_PLATFORM_TRAINING.md).

## Make targets reference

```bash
make help                     # full list with descriptions

# Core
make dev                      # UI + API foreground
make dev-d                    # UI + API detached
make trainer                  # host trainer worker, MLX backend (JSON logs)
make trainer-cuda             # trainer worker, CUDA backend (Linux + NVIDIA only)
make smoke-model MODEL=<key>  # 30-iter smoke test of a catalog model on this Mac
make ratchet                  # host autoresearch worker
make exporter                 # host GGUF exporter worker

# Observability (Phase L) — Loki + Grafana + Prometheus + Promtail
make obs-up                   # bring the overlay up
make obs-down                 # tear it down
make grafana                  # open Grafana in your browser
make prometheus               # open Prometheus
make loki-explore             # open Grafana Explore with Loki preselected

# Authentication (Phase M)
make auth ENABLED=true        # bring up Keycloak+OPA AND turn enforcement ON
make auth ENABLED=false       # bring up Keycloak+OPA, enforcement OFF
make auth-down                # stop Keycloak + OPA
make auth-token               # print a JWT for admin@local (for curl testing)
make opa-test                 # run the 18 Rego policy unit tests
make admin-panel              # open /admin/users (needs auth ENABLED + admin role)

# MCP server (Claude Desktop / Cursor / Claude Code)
make mcp-up                   # start the HTTP-transport container
make mcp-down                 # stop it
make mcp-logs                 # tail it

# Hermes + skills
make install-hermes           # Ollama + qwen3 + Hermes binary
make hermes-install-skills    # mirror .hermes-skills/ → ~/.hermes/skills/

# Data
make seed-data                # copy the 6 starter datasets into data/datasets/
make download-base-model      # pull the default base model from HF

# Cleanup
make clean                    # remove caches + node_modules + .venv
make nuke                     # also stop EVERY stack and wipe Docker volumes
```

## Enabling authentication (Phase M) — 5-step crisp guide

By default `SLM_FORGE_AUTH_ENABLED=true` in `.env` so the API expects valid JWTs. The auth-stack containers (Keycloak + OPA) live behind a Docker Compose profile and are not started by `make dev` alone — bring them up with `make auth`.

> **Want to disable auth temporarily?** Flip the flag: `SLM_FORGE_AUTH_ENABLED=false` in `.env`, `make down && make dev`. The API will fall back to a synthetic admin user for every request — useful when you're just kicking the tyres.

### Step 1 — Start Keycloak + OPA

```bash
make auth ENABLED=true
```

This brings up two containers behind the `auth` Compose profile:

- **Keycloak** on `http://localhost:8080` — admin console at `/admin/`, login `admin / admin`.
- **OPA** on `http://localhost:8181` — REPL + decision logs.

The realm `slm-forge` is auto-imported on first start with six roles (`admin`, `data_engineer`, `domain_expert`, `devops`, `operations`, `support`) and two seed users:

One seed user per role — log in as whichever you want to see role-based UI gating in action (tabs and buttons the role can't use are hidden):

| Username | Password | Role | What they can do |
|---|---|---|---|
| `admin@local` | `admin1234` | `admin` | Everything. |
| `engineer@local` | `engineer` | `data_engineer` | Datasets CRUD · experiments CRUD · runs read+cancel · execute exports · chat RW. No Maintenance. |
| `expert@local` | `expert123` | `domain_expert` | Read most things · update dataset READMEs · research CRUD · chat RW. Cannot create runs/exports. |
| `devops@local` | `devops123` | `devops` | Logs RW · settings RW · runs read. No datasets/experiments tabs. |
| `ops@local` | `ops12345` | `operations` | Read most things + execute exports. Cannot create. |
| `support@local` | `support1` | `support` | Read-only across the board. |

### Step 2 — Sign in to the SLM-Forge UI

Open `http://localhost:5173`. The user badge in the top-right says **"Sign in"**. Click it → you're redirected to Keycloak's login page → sign in as `admin@local` / `admin1234` → redirected back to the dashboard with your email + role pill showing.

From this point every request from the browser carries a JWT in the `Authorization: Bearer ...` header. The API verifies the signature against Keycloak's JWKS endpoint (cached for 5 minutes).

### Step 3 — Verify enforcement is active

Try a destructive action while logged in as `engineer@local` (data_engineer role):

- Go to `/exports` → try to delete any export → **403 Forbidden** with toast: `"data_engineer cannot delete exports — needs role: admin or operations."`
- Sign out and sign back in as `admin@local` → the same action succeeds.

The 403 message comes straight from OPA's `reason` rule, so it always explains *why* the action was denied and *what role* would be sufficient.

### Step 4 — Test from the command line

```bash
# Grab a fresh JWT for admin@local
TOKEN=$(make auth-token)

# Hit a protected endpoint
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
# → {"id":"admin@local","email":"admin@local","roles":["admin"],"groups":[]}

# Without the token → 401
curl http://localhost:8000/api/v1/auth/me
# → {"detail":"missing Authorization header"}
```

### Step 5 — Manage users via the admin UI

`/admin/users` is gated by `<RequireRole role="admin">`. Visit it as `admin@local` to see the Keycloak users table. For deeper management (creating users, assigning roles), use Keycloak's admin console directly at `http://localhost:8080/admin/master/console/`.

### Roles & permissions matrix

| Role | datasets | experiments | runs | exports | logs | settings | research | chat |
|---|---|---|---|---|---|---|---|---|
| **admin** | CRUD | CRUD | CRUD | CRUD | RW | RW | CRUD | RW |
| **data_engineer** | CRUD | CRUD | R + cancel | execute | R | — | R | RW |
| **domain_expert** | R + update README | R | R | R | — | — | RW | RW |
| **devops** | — | — | R | — | RW | RW | R | R |
| **operations** | R | R | R | execute | R | — | R | R |
| **support** | R | R | R | R | R | — | R | R |

Each cell maps to one or more `(action, resource)` rules in `policies/role_matrix.rego`. Run `make opa-test` to verify the 18 unit tests pass after any policy change.

### Troubleshooting auth

| Symptom | Fix |
|---|---|
| Sign-in redirects loop forever | Keycloak isn't up; run `make auth ENABLED=true` and wait ~15 s for the import to finish. |
| 401 on every API call after sign-in | Token expired (default 1 h). Sign out + back in; the silent-renew helper kicks in after the first refresh cycle. |
| 403 on actions you should be able to do | Check your role: `make auth-token | jq -R 'split(".")[1] | @base64d | fromjson | .realm_access.roles'`. |
| Can't reach Keycloak from the API | `docker compose --profile auth ps` — should show `keycloak` healthy. Check `KEYCLOAK_URL` in `.env`. |
| Need to disable auth fast | `SLM_FORGE_AUTH_ENABLED=false` in `.env` → `make down && make dev`. |

Full operator runbook: [`docs/AUTH.md`](docs/AUTH.md).

## Observability quickstart (Phase L)

```bash
make obs-up                   # Loki + Promtail + Prometheus + Grafana, ports 3001/9090/3100
make grafana                  # open http://localhost:3001 (admin/admin)
```

Three dashboards are pre-imported: **Overview** (request rate, p95 latency, heartbeats), **Runs Detail** (train/val/canary loss by run_id), **Logs Explorer** (LogQL with starter queries).

Detailed setup: [`docs/OBSERVABILITY_SETUP.md`](docs/OBSERVABILITY_SETUP.md).

## MCP integration

```bash
make mcp-up                   # start the HTTP-transport container on :8765
```

Then add SLM-Forge to your MCP client config (Claude Desktop / Cursor / Claude Code CLI). 39 tools are exposed: datasets, runs (incl. `start_run` with mlx/cuda backend routing), the model catalog, experiments, exports, hermes skills, multi-step agents, R&D research.

Detailed setup: [`docs/MCP_SETUP.md`](docs/MCP_SETUP.md).

## Tool calling on your fine-tuned models

Once a GGUF is exported you can do tool / function calling against it via Ollama. Which base models support it, how to import a Modelfile, full Python + curl examples, and the fine-tuning caveats that break tool calling — all documented in [`docs/TOOL_CALLING_GUIDE.md`](docs/TOOL_CALLING_GUIDE.md).

## Documentation map

- [`docs/PLAN.md`](docs/PLAN.md) — master plan, every phase A–N + L + M tracked.
- [`docs/MULTI_PLATFORM_TRAINING.md`](docs/MULTI_PLATFORM_TRAINING.md) — multi-backend training design (MLX + CUDA), feasibility tables, phases O–S (specs in `docs/specs/`).
- [`docs/MARKET_ANALYSIS.md`](docs/MARKET_ANALYSIS.md) — 3,000-word competitor study.
- [`docs/AUTH.md`](docs/AUTH.md) — Keycloak + OPA operator runbook.
- [`docs/OBSERVABILITY_SETUP.md`](docs/OBSERVABILITY_SETUP.md) — Prometheus / Grafana / Loki / Promtail setup.
- [`docs/MCP_SETUP.md`](docs/MCP_SETUP.md) — Claude Desktop / Cursor integration.
- [`docs/TOOL_CALLING_GUIDE.md`](docs/TOOL_CALLING_GUIDE.md) — tool calling on fine-tuned GGUFs.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — original architecture write-up.
- [`docs/SETUP.md`](docs/SETUP.md) — detailed setup, troubleshooting.
- [`docs/IPHONE_DEPLOY.md`](docs/IPHONE_DEPLOY.md) — getting your model onto iPhone.
- [`COMMIT_MESSAGE.md`](COMMIT_MESSAGE.md) — the megacommit release notes.

## What's intentionally NOT here

- ❌ Kubernetes / ArgoCD — single-machine tool, no cluster. Auth + observability are container-native so a future K8s lift is straightforward.
- ❌ Multi-GPU training — one GPU per worker (Apple Silicon unified memory, or a single CUDA GPU per remote worker).
- ❌ RLHF PPO — DPO works, full PPO needs a cluster.

## License

MIT
