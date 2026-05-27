# Setup — Phase 1

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **uv** | Fast Python dep manager | `brew install uv` |
| **Node 22+** | React build | `brew install node` |
| **Docker Desktop** | UI + API containers | https://www.docker.com/products/docker-desktop |
| **Homebrew** | macOS package manager | https://brew.sh |
| **Python 3.12+** | uv will install if missing | (auto) |

## First run (one-time)

```bash
git clone git@github.com:amitsahuwin/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration

make setup                # creates uv.lock + package-lock.json, installs all deps
make install-hermes       # Ollama + qwen2.5-coder:14b (used in Phase 2)
make seed-data            # creates dataset files in data/datasets/
make download-base-model  # ~1.5 GB Gemma 3n E2B → ~/.cache/huggingface
```

## Daily loop — two terminals required

```bash
# Terminal 1: UI + API (Docker, with live reload)
make dev

# Terminal 2: host trainer worker (needs Metal/MPS access)
make trainer
```

Open http://localhost:5173.

## Running your first training

1. UI → click "+ New Run" (top-right) or visit `/runs/new`
2. Pick `stock-analyst` dataset
3. Defaults are fine for a smoke test (200 iters, LoRA, Gemma 3n E2B)
4. Click "Start training"
5. You're redirected to `/runs/<id>` with live loss curve

The trainer terminal shows live `mlx_lm.lora` output. The first run takes a few minutes to load the model into memory; subsequent runs are fast.

## Switching base model

The model dropdown shows the catalogue defined in `apps/api/routers/models.py`. To add a new model, edit `CATALOG` in that file (no UI rebuild needed — just refresh).

## Hermes provider switch

Default is local Ollama. To switch to Groq's free tier:

```bash
export GROQ_API_KEY=gsk_...
hermes config set provider groq
hermes config set model qwen-2.5-coder-32b
hermes config set api_key $GROQ_API_KEY
```

## Troubleshooting

- **`make trainer` says "mlx_lm.lora not found"** → `uv sync --extra trainer`
- **Trainer fails with "model not found"** → `make download-base-model` first
- **Training is very slow** → make sure you're NOT running the trainer in Docker; it must be on host. Check `ps aux | grep mlx_lm` and confirm it's running on your Mac, not inside a container.
- **First training step takes 60+ seconds** → normal, that's model load. Subsequent steps are fast.
- **Port 8000 already in use** → `lsof -ti:8000 | xargs kill`
- **Port 5173 already in use** → `lsof -ti:5173 | xargs kill`
- **Docker "Cannot connect"** → start Docker Desktop
- **SSE stream stops mid-training** → browsers throttle background tabs; keep the page focused, or refresh to resume
- **`make seed-data` says missing files** → the patch should have populated `data/datasets/stock-analyst/`. Verify with `ls data/datasets/stock-analyst/`
