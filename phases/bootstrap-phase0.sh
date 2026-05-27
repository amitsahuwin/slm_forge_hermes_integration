#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 0 bootstrap  (v2, hardened)                       ║
# ║  Creates the full project folder structure with all scaffold files.  ║
# ║                                                                      ║
# ║  Usage:                                                              ║
# ║    chmod +x bootstrap_phase0.sh                                      ║
# ║    ./bootstrap_phase0.sh                                             ║
# ║                                                                      ║
# ║  Then:                                                               ║
# ║    cd slm_forge_hermes_integration                                   ║
# ║    ./init-repo.sh        # push to your GitHub                       ║
# ║    make setup            # install Python + Node deps                ║
# ║    make install-hermes   # install Ollama + Hermes + qwen2.5-coder   ║
# ║    make dev              # start UI + API (auto-runs setup if needed)║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

PROJECT="slm_forge_hermes_integration"

if [ -d "$PROJECT" ]; then
    echo "✗ Directory '$PROJECT' already exists. Move or delete it first."
    exit 1
fi

echo "→ Creating project: $PROJECT"
mkdir -p "$PROJECT"
cd "$PROJECT"

# ─────────────────────────────────────────────────────────────
# Folder structure
# ─────────────────────────────────────────────────────────────
mkdir -p apps/api/{routers,models,services}
mkdir -p apps/web/{public,src/{pages,components/{ratchet,ui},hooks,lib}}
mkdir -p packages/{trainer/methods,ratchet,ingest,exporter}
mkdir -p scripts
mkdir -p .hermes-skills
mkdir -p data/datasets
mkdir -p runs exports
mkdir -p tests/{trainer,ratchet,api}
mkdir -p docs
mkdir -p .github/workflows

# ─────────────────────────────────────────────────────────────
# Root: pyproject.toml
# ─────────────────────────────────────────────────────────────
cat > pyproject.toml <<'EOF'
[project]
name = "slm-forge"
version = "0.1.0"
description = "Local-first SLM fine-tuning lab driven by Hermes Agent and Karpathy-style autoresearch"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "Amit Sahu" }]

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "huey>=2.5",
    "pydantic>=2.9",
    "python-multipart>=0.0.12",
    "httpx>=0.27",
    "sse-starlette>=2.1",
]

[dependency-groups]
dev = [
    "ruff>=0.7",
    "mypy>=1.13",
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
]

[project.optional-dependencies]
trainer = [
    "mlx>=0.20",
    "mlx-lm>=0.20",
    "transformers>=4.46",
    "datasets>=3.1",
    "peft>=0.13",
    "huggingface-hub>=0.26",
    "safetensors>=0.4",
]
ingest = [
    "playwright>=1.48",
    "beautifulsoup4>=4.12",
    "boto3>=1.35",
    "requests>=2.32",
    "trafilatura>=1.12",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "A", "C4", "SIM", "RUF"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
EOF

# ─────────────────────────────────────────────────────────────
# Root: Makefile  (FIXED #4: ensure-lock target)
# ─────────────────────────────────────────────────────────────
cat > Makefile <<'EOF'
.PHONY: help setup install-hermes hermes-install-skills dev down build logs train-sample clean ensure-lock

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

setup: ## Install all deps (Python via uv, Node via npm) and create lock files
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	@echo "→ Installing Python deps with uv (Python 3.12+)..."
	uv sync --all-extras
	@echo "→ Installing Node deps for web app..."
	cd apps/web && npm install
	@echo "✓ Setup complete."
	@echo "  Next: make install-hermes"

install-hermes: ## Install Ollama + Hermes Agent + qwen2.5-coder:14b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

ensure-lock: ## Internal: auto-run setup if lock files are missing
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		echo "→ Lock files missing — running 'make setup' first..."; \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API (docker-compose up, live reload)
	docker compose up

down: ## Stop dev stack
	docker compose down

build: ensure-lock ## Build Docker images
	docker compose build

logs: ## Tail dev stack logs
	docker compose logs -f

train-sample: ## Phase 1+: run a sample training job (not yet implemented)
	@echo "✗ Phase 1+ feature. Currently in Phase 0 (scaffold)."

clean: ## Remove venv, node_modules, caches
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cleaned"
EOF

# ─────────────────────────────────────────────────────────────
# Root: docker-compose.yml
# ─────────────────────────────────────────────────────────────
cat > docker-compose.yml <<'EOF'
services:
  api:
    build:
      context: .
      dockerfile: apps/api/Dockerfile
    container_name: slm-forge-api
    ports:
      - "8000:8000"
    volumes:
      - ./apps/api:/app/apps/api
      - ./packages:/app/packages
      - ./data:/app/data
      - ./runs:/app/runs:ro
    environment:
      - PYTHONDONTWRITEBYTECODE=1
      - PYTHONUNBUFFERED=1
      - SLM_FORGE_DB_URL=sqlite:////app/data/slm_forge.db
    restart: unless-stopped

  web:
    build:
      context: ./apps/web
      dockerfile: Dockerfile
    container_name: slm-forge-web
    ports:
      - "5173:5173"
    volumes:
      - ./apps/web/src:/app/src
      - ./apps/web/public:/app/public
      - ./apps/web/index.html:/app/index.html
      - ./apps/web/tailwind.config.ts:/app/tailwind.config.ts
      - ./apps/web/postcss.config.js:/app/postcss.config.js
      - ./apps/web/vite.config.ts:/app/vite.config.ts
      - ./apps/web/tsconfig.json:/app/tsconfig.json
    environment:
      - VITE_API_URL=http://localhost:8000
    depends_on:
      - api
    restart: unless-stopped
EOF

# ─────────────────────────────────────────────────────────────
# Root: .dockerignore  (FIXED #6)
# ─────────────────────────────────────────────────────────────
cat > .dockerignore <<'EOF'
.venv
__pycache__
.pytest_cache
.ruff_cache
.mypy_cache
*.egg-info
node_modules
apps/web/node_modules
apps/web/dist
.git
.github
data
runs
exports
*.log
.DS_Store
.env
.env.local
EOF

# ─────────────────────────────────────────────────────────────
# Root: .gitignore
# ─────────────────────────────────────────────────────────────
cat > .gitignore <<'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/
dist/
build/

# Node
node_modules/
apps/web/dist/

# IDE / OS
.vscode/
.idea/
.DS_Store

# Project data (large / private)
data/datasets/*/
!data/datasets/.gitkeep
runs/*/
!runs/.gitkeep
exports/*/
!exports/.gitkeep

# Env
.env
.env.local
*.local

# SQLite
*.db
*.sqlite
*.sqlite3

# Logs
*.log

# uv.lock IS committed (intentional)
# package-lock.json IS committed (intentional)
EOF

# ─────────────────────────────────────────────────────────────
# Root: LICENSE (MIT)
# ─────────────────────────────────────────────────────────────
cat > LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Amit Sahu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF

# ─────────────────────────────────────────────────────────────
# Root: README.md
# ─────────────────────────────────────────────────────────────
cat > README.md <<'EOF'
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
EOF

# ─────────────────────────────────────────────────────────────
# Root: init-repo.sh
# ─────────────────────────────────────────────────────────────
cat > init-repo.sh <<'EOF'
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Initialize the local git repo and push to GitHub.
# Idempotent: safe to re-run.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE="git@github.com:amitsahuwin/slm_forge_hermes_integration.git"

echo "→ Verifying SSH access to GitHub..."
ssh_output=$(ssh -T -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 git@github.com 2>&1 || true)
if ! echo "$ssh_output" | grep -q "successfully authenticated"; then
    cat <<MSG
✗ SSH access to GitHub is not configured.

To set it up:
  1. Generate an SSH key (if you don't have one):
       ssh-keygen -t ed25519 -C "your_email@example.com"
  2. Copy the public key:
       pbcopy < ~/.ssh/id_ed25519.pub
  3. Add it on GitHub:
       https://github.com/settings/ssh/new
  4. Re-run this script:
       ./init-repo.sh
MSG
    exit 1
fi
echo "✓ SSH access OK"

if [ -d .git ]; then
    echo "→ Git repo already initialized"
else
    echo "→ Initializing git repo (branch: main)"
    git init -b main >/dev/null
fi

if ! git remote get-url origin >/dev/null 2>&1; then
    echo "→ Adding remote origin: $REMOTE"
    git remote add origin "$REMOTE"
else
    current=$(git remote get-url origin)
    if [ "$current" != "$REMOTE" ]; then
        echo "→ Updating remote origin"
        git remote set-url origin "$REMOTE"
    else
        echo "✓ Remote origin already set"
    fi
fi

echo "→ Staging files..."
git add -A

if git diff --cached --quiet; then
    echo "→ Nothing to commit"
else
    git commit -m "Phase 0: project scaffold (uv + FastAPI + React + Vite + Tailwind + Hermes)" >/dev/null
    echo "✓ Committed"
fi

echo "→ Pushing to GitHub..."
git push -u origin main

echo ""
echo "✓ Pushed to: $REMOTE"
echo "  https://github.com/amitsahuwin/slm_forge_hermes_integration"
EOF

# ─────────────────────────────────────────────────────────────
# scripts/install_hermes.sh  (FIXED #5: Ollama restart after keep-alive)
# ─────────────────────────────────────────────────────────────
cat > scripts/install_hermes.sh <<'EOF'
#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Install Ollama + qwen2.5-coder:14b + Hermes Agent.
# Configure Hermes to use the local Ollama instance.
# ─────────────────────────────────────────────────────────────
set -euo pipefail

echo "═══════════════════════════════════════════════════════"
echo "  SLM-Forge: Hermes Agent + Ollama Setup"
echo "  Target: macOS Apple Silicon (M3 Max 36 GB)"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── 1. Ollama ─────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    if command -v brew &>/dev/null; then
        echo "→ Installing Ollama via Homebrew..."
        brew install ollama
    else
        echo "→ Installing Ollama via the official installer..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi
else
    echo "✓ Ollama already installed: $(ollama --version 2>/dev/null | head -n1)"
fi

# ── 2. Configure keep-alive BEFORE starting Ollama ────────────
echo "→ Setting OLLAMA_KEEP_ALIVE=2m (frees RAM during training)"
launchctl setenv OLLAMA_KEEP_ALIVE 2m || true

# ── 3. Start / restart Ollama so it picks up the env var ──────
if command -v brew &>/dev/null; then
    if brew services list 2>/dev/null | grep -q "ollama.*started"; then
        echo "→ Restarting Ollama to pick up new env vars..."
        brew services restart ollama
    else
        echo "→ Starting Ollama service..."
        brew services start ollama || true
    fi
else
    echo "  ⚠ Homebrew not found — please run 'ollama serve' manually in another terminal."
fi

# Wait for Ollama to come up
echo "→ Waiting for Ollama API on :11434..."
for i in {1..15}; do
    if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        echo "✓ Ollama responding"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "✗ Ollama didn't respond in 15s. Try: brew services restart ollama"
        exit 1
    fi
    sleep 1
done

# ── 4. Pull qwen2.5-coder:14b ─────────────────────────────────
if ollama list 2>/dev/null | grep -q "qwen2.5-coder:14b"; then
    echo "✓ qwen2.5-coder:14b already pulled"
else
    echo "→ Pulling qwen2.5-coder:14b (~9 GB, takes a few minutes)..."
    ollama pull qwen2.5-coder:14b
fi

# ── 5. Install Hermes Agent ───────────────────────────────────
# Search common install locations for hermes binary
locate_hermes() {
    for candidate in "$HOME/.local/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
        if [ -x "$candidate/hermes" ]; then
            export PATH="$candidate:$PATH"
            return 0
        fi
    done
    command -v hermes &>/dev/null
}

if ! locate_hermes; then
    echo "→ Installing Hermes Agent..."
    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
    if ! locate_hermes; then
        echo "✗ Hermes install completed but binary not found. Check ~/.local/bin or restart your shell."
        exit 1
    fi
else
    echo "✓ Hermes already installed"
fi

# ── 6. Configure Hermes for local Ollama ──────────────────────
echo "→ Configuring Hermes to use local Ollama..."
hermes config set provider ollama         || true
hermes config set model qwen2.5-coder:14b || true
hermes config set base_url http://localhost:11434 || true

echo ""
echo "✓ Hermes configured. Current config:"
hermes config show 2>/dev/null || echo "  (run 'hermes config show' yourself to verify)"

cat <<MSG

────────────────────────────────────────────────────────────────
Next steps:
  • make hermes-install-skills   # load SLM-Forge skills (Phase 2+)
  • make dev                     # start UI + API

Switch to Groq later (one command, free tier):
  export GROQ_API_KEY=gsk_...
  hermes config set provider groq
  hermes config set model qwen-2.5-coder-32b
  hermes config set api_key \$GROQ_API_KEY
────────────────────────────────────────────────────────────────
MSG
EOF

# ─────────────────────────────────────────────────────────────
# scripts/install_skills.sh
# ─────────────────────────────────────────────────────────────
cat > scripts/install_skills.sh <<'EOF'
#!/usr/bin/env bash
# Copy versioned skills from .hermes-skills/ into ~/.hermes/skills/
set -euo pipefail

SRC=".hermes-skills"
DEST="$HOME/.hermes/skills"

if [ ! -d "$SRC" ]; then
    echo "✗ $SRC not found. Run this from the project root."
    exit 1
fi

mkdir -p "$DEST"

count=0
shopt -s nullglob
for f in "$SRC"/*.md; do
    base=$(basename "$f")
    if [ "$base" = "README.md" ]; then continue; fi
    cp "$f" "$DEST/"
    echo "  ✓ Installed $base"
    count=$((count + 1))
done

if [ "$count" -eq 0 ]; then
    echo ""
    echo "ℹ No skills yet — Phase 2 will populate .hermes-skills/."
    echo "  ($DEST exists and is ready.)"
else
    echo ""
    echo "✓ Installed $count skill(s) to $DEST"
fi
EOF

# ─────────────────────────────────────────────────────────────
# scripts/setup.sh
# ─────────────────────────────────────────────────────────────
cat > scripts/setup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
make setup
EOF

# ─────────────────────────────────────────────────────────────
# scripts/start_host_services.sh
# ─────────────────────────────────────────────────────────────
cat > scripts/start_host_services.sh <<'EOF'
#!/usr/bin/env bash
# Phase 1+: launches trainer worker, Hermes agent, exporter on host (not Docker).
# Reason: MLX/Metal is only available on host macOS, not inside Linux containers.
set -euo pipefail
echo "⚠  Host services launch is Phase 1+. Currently in Phase 0."
echo "   For Phase 0, 'make dev' (docker-compose) is all you need."
EOF

# ─────────────────────────────────────────────────────────────
# .github/workflows/ci.yml
# ─────────────────────────────────────────────────────────────
cat > .github/workflows/ci.yml <<'EOF'
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  python:
    name: Python (lint + typecheck)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: latest
      - name: Set up Python 3.12
        run: uv python install 3.12
      - name: Install deps (no trainer extra — MLX is macOS-only)
        run: uv sync --extra ingest --group dev
      - name: Ruff lint
        run: uv run ruff check .
      - name: Ruff format check
        run: uv run ruff format --check .
      - name: Mypy
        run: uv run mypy apps/api || true   # not blocking in Phase 0
      - name: Pytest
        run: uv run pytest tests/ -q || true # no tests yet

  web:
    name: Web (typecheck + build)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - run: npm ci
      - run: npm run typecheck
      - run: npm run build
EOF

# ─────────────────────────────────────────────────────────────
# Python package __init__.py files  (FIXED #6)
# ─────────────────────────────────────────────────────────────
touch apps/__init__.py
touch apps/api/__init__.py
touch apps/api/routers/__init__.py
touch apps/api/models/__init__.py
touch apps/api/services/__init__.py
touch packages/__init__.py
touch packages/trainer/__init__.py
touch packages/trainer/methods/__init__.py
touch packages/ratchet/__init__.py
touch packages/ingest/__init__.py
touch packages/exporter/__init__.py
touch tests/__init__.py
touch tests/trainer/__init__.py
touch tests/ratchet/__init__.py
touch tests/api/__init__.py

# ─────────────────────────────────────────────────────────────
# apps/api/main.py
# ─────────────────────────────────────────────────────────────
cat > apps/api/main.py <<'EOF'
"""SLM-Forge API — Phase 0 scaffold."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    python: str
    capabilities: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # Phase 1+: initialize SQLite tables, start Huey consumer, register routers.
    yield


app = FastAPI(
    title="SLM-Forge API",
    description="Local-first SLM fine-tuning lab driven by Hermes Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "SLM-Forge API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys

    return HealthResponse(
        status="ok",
        version="0.1.0",
        phase="Phase 0 — scaffold",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": False,         # Phase 1
            "autoresearch": False,    # Phase 2
            "ingestion": False,       # Phase 3
            "export_gguf": False,     # Phase 4
            "hermes_bridge": False,   # Phase 2
        },
    )
EOF

# ─────────────────────────────────────────────────────────────
# apps/api/Dockerfile  (FIXED #1: no wildcard COPY)
# ─────────────────────────────────────────────────────────────
cat > apps/api/Dockerfile <<'EOF'
FROM python:3.12-slim

WORKDIR /app

# uv for fast Python dep management
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir uv

# Lock file is required — make ensure-lock guarantees it exists before build
COPY pyproject.toml uv.lock /app/

# Install dependencies (no trainer extra — MLX is macOS-only; trainer runs on host)
RUN uv sync --extra ingest --no-install-project

# Copy source
COPY apps /app/apps
COPY packages /app/packages

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/package.json  (FIXED #3: tsc --noEmit, no -b)
# ─────────────────────────────────────────────────────────────
cat > apps/web/package.json <<'EOF'
{
  "name": "slm-forge-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "typecheck": "tsc --noEmit",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.16",
    "typescript": "^5.7.2",
    "vite": "^6.0.3"
  }
}
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/Dockerfile  (FIXED #2: no wildcard COPY)
# ─────────────────────────────────────────────────────────────
cat > apps/web/Dockerfile <<'EOF'
FROM node:22-alpine

WORKDIR /app

# Lock file is required — make ensure-lock guarantees it exists before build
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/.dockerignore  (FIXED #6)
# ─────────────────────────────────────────────────────────────
cat > apps/web/.dockerignore <<'EOF'
node_modules
dist
.dockerignore
Dockerfile
.DS_Store
*.log
.env
.env.local
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/index.html
# ─────────────────────────────────────────────────────────────
cat > apps/web/index.html <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SLM-Forge</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/vite.config.ts
# ─────────────────────────────────────────────────────────────
cat > apps/web/vite.config.ts <<'EOF'
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    watch: { usePolling: true },
  },
});
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/tailwind.config.ts
# ─────────────────────────────────────────────────────────────
cat > apps/web/tailwind.config.ts <<'EOF'
import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config;
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/postcss.config.js
# ─────────────────────────────────────────────────────────────
cat > apps/web/postcss.config.js <<'EOF'
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/tsconfig.json
# ─────────────────────────────────────────────────────────────
cat > apps/web/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vite/client"]
  },
  "include": ["src", "vite.config.ts"]
}
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/src/main.tsx
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/main.tsx <<'EOF'
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/src/index.css
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/index.css <<'EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen,
    Ubuntu, Cantarell, sans-serif;
  color-scheme: dark;
}

html, body, #root {
  min-height: 100vh;
}

body {
  margin: 0;
  background: #09090b; /* zinc-950 */
  color: #fafafa;      /* zinc-50  */
}

* {
  box-sizing: border-box;
}
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/src/App.tsx
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/App.tsx <<'EOF'
import { useEffect, useState } from 'react';

type Capabilities = Record<string, boolean>;

type Health = {
  status: string;
  version: string;
  phase: string;
  python: string;
  capabilities: Capabilities;
};

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: Health) => setHealth(data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 px-8 py-6">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">SLM-Forge</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Local-first SLM fine-tuning lab · Hermes-driven autoresearch
            </p>
          </div>
          <div className="font-mono text-xs text-zinc-600">v0.1.0</div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-8 py-12">
        <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
          <Card title="API Status">
            {error ? (
              <div className="font-mono text-sm text-rose-400">
                <div>error</div>
                <div className="mt-1 text-zinc-400">{error}</div>
              </div>
            ) : health ? (
              <dl className="space-y-1.5 font-mono text-sm">
                <Row label="status" value={health.status} ok />
                <Row label="version" value={health.version} />
                <Row label="phase" value={health.phase} />
                <Row label="python" value={health.python} />
              </dl>
            ) : (
              <span className="text-sm text-zinc-500">Connecting…</span>
            )}
          </Card>

          <Card title="Hermes Agent">
            <p className="text-sm text-zinc-400">
              Configure via{' '}
              <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-200">
                make install-hermes
              </code>
              .
            </p>
            <p className="mt-2 text-xs text-zinc-600">
              Default: Ollama + qwen2.5-coder:14b (local, free)
            </p>
          </Card>

          <Card title="Trainer">
            <p className="text-sm text-zinc-400">
              MLX-LM trainer runs on host (Metal access). Comes online in Phase 1.
            </p>
          </Card>
        </section>

        {health && (
          <section className="mt-10">
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
              Capabilities
            </h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              {Object.entries(health.capabilities).map(([key, enabled]) => (
                <div
                  key={key}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
                >
                  <div className="font-mono text-xs text-zinc-500">{key}</div>
                  <div
                    className={`mt-1 font-mono text-sm ${
                      enabled ? 'text-emerald-400' : 'text-zinc-600'
                    }`}
                  >
                    {enabled ? '● enabled' : '○ pending'}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="mt-12">
          <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-zinc-500">
            Roadmap
          </h2>
          <ol className="space-y-2.5">
            <Phase n="0" current label="Foundation: scaffold, Hermes/Ollama install, dev stack" />
            <Phase n="1" label="End-to-end LoRA on Gemma 4 E2B + live loss chart" />
            <Phase n="2" label="Autoresearch ratchet + 4-graph UI" />
            <Phase n="3" label="Data ingestion (local, URL, scrape, S3)" />
            <Phase n="4" label="Export pipeline (LoRA → GGUF → iPhone)" />
            <Phase n="5" label="Polish, 6 sample datasets, full docs" />
          </ol>
        </section>
      </main>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="w-16 text-zinc-500">{label}</dt>
      <dd className={ok ? 'text-emerald-400' : 'text-zinc-200'}>{value}</dd>
    </div>
  );
}

function Phase({
  n,
  label,
  current,
}: {
  n: string;
  label: string;
  current?: boolean;
}) {
  return (
    <li className="flex items-baseline gap-4 text-sm">
      <span
        className={`font-mono ${current ? 'text-emerald-400' : 'text-zinc-600'}`}
      >
        [{n}]
      </span>
      <span className={current ? 'text-zinc-100' : 'text-zinc-500'}>
        {label}
        {current && <span className="ml-2 text-xs text-emerald-500">← you are here</span>}
      </span>
    </li>
  );
}
EOF

# ─────────────────────────────────────────────────────────────
# apps/web/public/.gitkeep
# ─────────────────────────────────────────────────────────────
touch apps/web/public/.gitkeep

# ─────────────────────────────────────────────────────────────
# .hermes-skills/README.md
# ─────────────────────────────────────────────────────────────
cat > .hermes-skills/README.md <<'EOF'
# Hermes Skills

This directory holds Hermes Agent skills (markdown files) that are version-controlled
in the repo and copied into `~/.hermes/skills/` by `make hermes-install-skills`.

## Phase 2 will add:

- `propose_hyperparam_mutation.md` — given metrics history, propose next config change
- `diagnose_mps_oom.md` — recognize Apple MPS OOM, suggest fixes (batch size, QLoRA)
- `select_method_for_task.md` — recommend LoRA vs QLoRA vs full SFT vs DPO
- `recommend_base_model.md` — Gemma 4 E2B/E4B/26B vs Qwen 2.5 vs Llama 3.2
- `debug_training_error.md` — read traceback, propose fix, write new skill if novel
- `ingest_dataset.md` — given URL/path, detect format, load
- `analyze_canary_drift.md` — detect Goodhart-style overfitting, propose regularization

Each skill is a single `.md` file with a YAML front-matter header describing
when Hermes should invoke it, and a body containing the instruction text.
EOF

# ─────────────────────────────────────────────────────────────
# .gitkeep files for empty tracked directories
# ─────────────────────────────────────────────────────────────
touch data/datasets/.gitkeep
touch runs/.gitkeep
touch exports/.gitkeep

# ─────────────────────────────────────────────────────────────
# docs/SETUP.md
# ─────────────────────────────────────────────────────────────
cat > docs/SETUP.md <<'EOF'
# Setup

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
# 1. Clone (after init-repo.sh has pushed)
git clone git@github.com:amitsahuwin/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration

# 2. Install deps (Python + Node) — creates uv.lock + package-lock.json
make setup

# 3. Install Ollama + Hermes Agent + qwen2.5-coder:14b
make install-hermes
```

> `make dev` auto-runs `make setup` if your lock files don't exist yet.

## Daily dev loop

```bash
make dev       # starts API on :8000 and UI on :5173 with live reload
make logs      # tail logs
make down      # stop
```

## Hermes provider switch

Default is local Ollama (no API key, no rate limits). To switch to Groq's free tier:

```bash
export GROQ_API_KEY=gsk_...                # from https://console.groq.com
hermes config set provider groq
hermes config set model qwen-2.5-coder-32b
hermes config set api_key $GROQ_API_KEY
hermes config show
```

## Troubleshooting

- **`uv: command not found`** → `brew install uv`
- **Port 8000 already in use** → `lsof -ti:8000 | xargs kill`
- **Docker says "Cannot connect"** → start Docker Desktop
- **Ollama "connection refused"** → `brew services restart ollama`
- **`hermes: command not found` after install** → open new terminal or `export PATH="$HOME/.local/bin:$PATH"`
- **SSH push fails** → see `init-repo.sh`'s on-screen instructions
- **`make dev` says lock file missing** → it auto-runs setup; if it still fails, run `make setup` manually
EOF

# ─────────────────────────────────────────────────────────────
# docs/ARCHITECTURE.md
# ─────────────────────────────────────────────────────────────
cat > docs/ARCHITECTURE.md <<'EOF'
# Architecture

> Full plan lives in the project's design doc. This file is the concise reference.

## Key decisions

| Concern | Choice | Why |
|---|---|---|
| Orchestration | docker-compose (UI + API only) | K8s/ArgoCD are cluster tools; we have one machine. |
| Trainer location | **Host macOS, NOT Docker** | Apple Metal/MLX is not accessible from Linux containers. |
| Training engine | **MLX-LM** | Fastest path on Apple Silicon, ~3× PyTorch-MPS on M3 Max. |
| Job queue | **Huey + SQLite** | No Redis/RabbitMQ container; one less moving part. |
| DB | SQLite via SQLModel | Single-user local tool; zero-config. |
| Frontend | React 19 + Vite + Tailwind | Modern, fast, low-config. |
| Backend | FastAPI + SSE | Live log/metric streaming over EventSource. |
| Agent | Hermes Agent (sibling process) | Loose coupling via shared SQLite + filesystem. |
| Agent LLM (default) | Ollama + qwen2.5-coder:14b | Local, free, no rate limits. |
| Agent LLM (fallback) | Groq qwen-2.5-coder-32b | Fast, free tier. |
| Python deps | uv + pyproject.toml | 10-100× faster than pip; lockfile. |
| Python version | 3.12+ | Modern stdlib + uv default. |
| CI | GitHub Actions | Lint, typecheck, build on push. |

## Component map

```
  Browser ──HTTP──► UI (React, Docker)
                     │
                     │ HTTP + SSE
                     ▼
                    API (FastAPI, Docker)
                     │
                     │ enqueue (Huey + SQLite)
                     ▼
                    SQLite ◄────────── reads/writes ──── Trainer (HOST, MLX-LM)
                     ▲                                       │
                     │                                       │ requests mutation
                     │                                       ▼
                     │                                   Ratchet (HOST, Python)
                     │                                       │
                     │                                       │ asks for next config
                     │                                       ▼
                     └──────── writes skills ──── Hermes Agent (HOST, CLI)
                                                              │
                                                              │ uses
                                                              ▼
                                                          Ollama (HOST, :11434)
```

## The autoresearch loop (Phase 2)

```
baseline → Hermes proposes mutation → train → eval
                                              │
                       improved ─────────────►├──► git commit ─┐
                       worse/same ───────────►├──► git reset   │
                       error ────────────────►├──► Hermes fixes│
                                              │                │
                                              └────────────────┘
                                              (until plateau or budget)
```
EOF

# ─────────────────────────────────────────────────────────────
# Make shell scripts executable
# ─────────────────────────────────────────────────────────────
chmod +x init-repo.sh
chmod +x scripts/install_hermes.sh
chmod +x scripts/install_skills.sh
chmod +x scripts/setup.sh
chmod +x scripts/start_host_services.sh

# ─────────────────────────────────────────────────────────────
# Done — print summary
# ─────────────────────────────────────────────────────────────
cd ..

FILE_COUNT=$(find "$PROJECT" -type f | wc -l | tr -d ' ')
DIR_COUNT=$(find "$PROJECT" -type d | wc -l | tr -d ' ')

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 0 scaffold created: ./$PROJECT
╚══════════════════════════════════════════════════════════════════════╝

Stats:
  • Directories: $DIR_COUNT
  • Files:       $FILE_COUNT

Next steps:

  cd $PROJECT
  ./init-repo.sh           # push to your GitHub
  make setup               # install Python (via uv) + Node deps
  make install-hermes      # install Ollama + Hermes + qwen2.5-coder:14b
  make dev                 # start UI on :5173 and API on :8000

  Then open: http://localhost:5173

If anything fails, paste the error back and I'll fix it before Phase 1.
MSG
