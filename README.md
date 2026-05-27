# SLM-Forge

> Local-first SLM fine-tuning lab on Apple Silicon. **Phase 1: live training works.**

**Target:** MacBook Pro M3 Max, 36 GB · Python 3.12+ · React 19 · MLX-LM 0.30+

---

## Phase 1 quickstart

```bash
# One-time setup
./init-repo.sh                  # push to GitHub
make setup                      # uv + Node deps
make install-hermes             # Ollama + qwen2.5-coder:14b (Phase 2 prep)
make seed-data                  # copy sample datasets into data/datasets/
make download-base-model        # ~1.5 GB Gemma 3n E2B from HF

# Daily loop — two terminals
make dev                        # Terminal 1: UI + API in Docker
make trainer                    # Terminal 2: host trainer worker (Metal access)
```

Then open http://localhost:5173/runs/new, pick `stock-analyst` + default settings, click **Start training**, and watch the loss curve drop live.

---

## What's in Phase 1

- ✅ Sample dataset: `stock-analyst` (24 examples — smoke test scale)
- ✅ Host trainer worker: invokes `mlx_lm.lora`, streams stdout, posts metrics to API
- ✅ SQLite (via SQLModel) for runs + metrics
- ✅ REST API: `POST/GET /api/v1/runs`, `GET /api/v1/runs/{id}/metrics`
- ✅ SSE endpoint: `GET /api/v1/runs/{id}/stream` for live updates
- ✅ UI pages: Dashboard, Runs (list), New Run (form), Run Detail (live chart), Datasets
- ✅ Recharts-based live loss curve (train + val)
- ✅ Configurable: model, method (LoRA/DoRA/full SFT), iters, batch size, LR, num layers

## What's NOT in Phase 1 (next phases)

- ❌ Autoresearch ratchet loop (Phase 2)
- ❌ Hermes-driven hyperparameter mutation (Phase 2)
- ❌ Canary drift detection (Phase 2)
- ❌ Web/URL/S3 ingestion (Phase 3)
- ❌ GGUF export pipeline (Phase 4)
- ❌ Remaining 5 sample datasets (Phase 5)

---

## Why two terminals?

| Process | Where | Why |
|---|---|---|
| UI + API | Docker (`make dev`) | Lightweight, isolated |
| Trainer worker | Host (`make trainer`) | MLX needs Apple Metal/MPS — not available inside Linux containers |

This is a deliberate architectural choice — see `docs/ARCHITECTURE.md`.

---

## Project structure

```
.
├── apps/
│   ├── api/                # FastAPI: runs/metrics/datasets/models endpoints
│   └── web/                # React 19 + Vite + Tailwind + Recharts
├── packages/
│   ├── trainer/            # MLX-LM trainer worker (host process)
│   ├── ratchet/            # Autoresearch (Phase 2)
│   ├── ingest/             # Data ingestion (Phase 3)
│   └── exporter/           # GGUF export (Phase 4)
├── data/datasets/          # Datasets in mlx_lm.lora format
│   └── stock-analyst/      # train.jsonl + valid.jsonl + canary.jsonl
├── runs/                   # Per-run output (adapter/, config.yaml, training.log)
└── scripts/                # setup, seeding, model download
```
