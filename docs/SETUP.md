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
