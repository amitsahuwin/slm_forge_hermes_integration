# SLM-Forge

> Local-first fine-tuning lab for small language models on Apple Silicon. Hermes Agent drives autoresearch. One-click export to iPhone via PocketPal.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Built for Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-MLX-orange.svg)](https://github.com/ml-explore/mlx)

---

## What this is

A complete pipeline for fine-tuning small language models (Qwen 2.5 3B, Llama 3.2 3B) on your MacBook Pro, with a Hermes-agent-driven autoresearch loop that automatically explores hyperparameters, and a one-click GGUF export so you can run your fine-tuned model on your iPhone offline.

Built specifically for M3 Max with 36GB unified memory. Smaller Apple Silicon Macs work too with reduced model sizes.

## What it does

| Capability | Status |
|---|---|
| LoRA / DoRA / full SFT on Apple Silicon via MLX | ✓ |
| Autoresearch ratchet (Hermes-driven hyperparameter sweeps) | ✓ |
| Live training metrics + ratchet timeline graphs | ✓ |
| Data ingestion: upload, URL, web scrape, S3 | ✓ |
| Export to GGUF + quantize for iPhone | ✓ |
| Maintenance UI (disk usage, cleanup) | ✓ |
| 6 starter datasets (stock-analyst, code-review, email, recipes, medical QA, support classifier) | ✓ |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    macOS Host (M3 Max)                           │
│                                                                  │
│   ┌──────────┐    ┌──────────┐                                   │
│   │ React UI │───▶│ FastAPI  │ ← Docker                          │
│   └──────────┘    └────┬─────┘                                   │
│                        │ SQLite + Huey queue                     │
│                        │                                         │
│   ┌────────────────────▼─────────────────────────┐               │
│   │ Trainer  │ Ratchet  │ Exporter │ Hermes      │ ← host procs  │
│   │ (MLX-LM) │ (loop)   │ (GGUF)   │ Bridge      │   (Metal)     │
│   └──────────┴──────────┴──────────┴──────┬──────┘               │
│                                            │                     │
│   ┌────────────────────────────────────────▼────┐                │
│   │ Ollama : qwen3:30b-a3b (or any model)       │                │
│   └─────────────────────────────────────────────┘                │
└────────────────────────┬─────────────────────────────────────────┘
                         │ GGUF transfer
                         ▼
                  ┌─────────────┐
                  │   iPhone    │
                  │ PocketPal AI│
                  └─────────────┘
```

See `docs/ARCHITECTURE.md` for the full architecture write-up.

## Requirements

- macOS on Apple Silicon (M1/M2/M3 — M3 Max with 36GB unified memory is the development target)
- Python 3.12 or 3.13
- Node.js 20+
- Homebrew
- Docker Desktop for Mac
- ~30 GB free disk for models + exports

## Quick start

```bash
# 1. Clone
git clone git@github.com:<you>/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration

# 2. One-time setup
make setup                    # uv + Python + Node deps
make install-hermes           # Ollama + qwen3:30b-a3b
brew install llama.cpp        # GGUF tooling

# 3. Start everything (four terminals)
make dev                      # T1: UI on :5173, API on :8000
make trainer                  # T2: LoRA training worker
make ratchet                  # T3: autoresearch loop
make exporter                 # T4: GGUF export worker

# 4. Open the UI
open http://localhost:5173
```

## End-to-end walkthrough

```
1. Ingest a dataset            → /datasets/new
2. Start an autoresearch experiment   → /experiments/new
3. Watch the ratchet graph     → /experiments/:id
4. Export the winner to GGUF   → /runs/:id → "Export to GGUF"
5. Download Q4_K_M.gguf        → /exports
6. AirDrop to iPhone           → PocketPal AI → Add Local Model
```

## Documentation

- `docs/ARCHITECTURE.md` — why this architecture (and what we rejected)
- `docs/SETUP.md` — detailed setup, troubleshooting
- `docs/IPHONE_DEPLOY.md` — getting your model onto iPhone
- `docs/DEMO_SCRIPT.md` — 2-minute video walkthrough
- `docs/SCREENSHOTS.md` — UI captures (what to record)

## What's intentionally NOT here

- ❌ Kubernetes / ArgoCD — single-machine tool, no cluster
- ❌ Auth — local-only, single-user
- ❌ Multi-GPU — Apple Silicon unified memory only
- ❌ RLHF PPO — DPO works, full PPO needs cluster
- ❌ Production monitoring — this is a personal lab

## License

MIT
