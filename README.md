# SLM-Forge

> Local-first SLM fine-tuning lab on Apple Silicon, driven by **Hermes Agent** and **Karpathy-style autoresearch**, with one-click export to iPhone (PocketPal / Edge Gallery).

**Target hardware:** MacBook Pro M3 Max, 36 GB unified memory, 1 TB SSD
**Language:** Python 3.12+ · TypeScript 5.7+ · React 19
**License:** MIT

---

## Quickstart

```bash
# 1. Push the scaffold to your GitHub (one-time)
./init-repo.sh

# 2. Install Python + Node deps (creates uv.lock + package-lock.json)
make setup

# 3. Install Ollama + Hermes Agent + qwen2.5-coder:14b
make install-hermes

# 4. Install Hermes skills (Phase 2 will populate them)
make hermes-install-skills

# 5. Start the dev stack
make dev
```

Then open:
- UI: http://localhost:5173
- API: http://localhost:8000/docs

> `make dev` auto-runs `make setup` if your lock files are missing, so you can skip step 2 if you want.

---

## Phase 0 (current) — what works

- ✅ FastAPI backend with `/api/v1/health` endpoint
- ✅ React 19 + Vite + Tailwind frontend that fetches API status
- ✅ `docker compose up` brings both online
- ✅ `init-repo.sh` pushes to GitHub
- ✅ Ollama + Hermes + qwen2.5-coder:14b auto-install
- ✅ uv-managed Python 3.12+ deps
- ✅ GitHub Actions CI (lint, typecheck, build)

## Phase 1 (next) — coming up

- LoRA fine-tuning of Gemma 4 E2B via MLX-LM
- Sample dataset: `stock-analyst`
- Live loss curve streaming over SSE

See the full plan in `docs/ARCHITECTURE.md`.

---

## Project structure

```
.
├── apps/
│   ├── api/           # FastAPI backend
│   └── web/           # React + Vite + Tailwind frontend
├── packages/
│   ├── trainer/       # MLX-LM fine-tuning worker (Phase 1)
│   ├── ratchet/       # Autoresearch loop (Phase 2)
│   ├── ingest/        # Data ingestion (Phase 3)
│   └── exporter/      # GGUF export pipeline (Phase 4)
├── .hermes-skills/    # Hermes Agent skills (Phase 2+)
├── scripts/           # Setup + install scripts
├── docs/              # ARCHITECTURE, SETUP, USECASES, etc.
└── .github/workflows/ # CI
```

---

## Why this architecture (the short version)

- **No K8s, no ArgoCD** — single-machine local tool, those add zero value here
- **Trainer runs on host macOS, not in Docker** — Docker on Mac can't access Apple Metal/MLX (50-100× CPU slowdown otherwise)
- **Only UI + API are containerized** — keeps the lightweight stuff isolated, keeps the GPU-heavy stuff fast
- **Hermes runs as a sibling process** — loose coupling via shared SQLite; agent stays autonomous
- **Huey + SQLite for job queue** — no Redis/RabbitMQ container, one less moving part
- **MLX-LM as the training engine** — fastest path on Apple Silicon (~3× faster than PyTorch-MPS on your M3 Max)

See `docs/ARCHITECTURE.md` for the full reasoning.
