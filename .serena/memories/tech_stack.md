# Tech Stack

## Python (managed by `uv`, NEVER pip/python directly)
- Python `>=3.12` (project floor; uv installs managed 3.12 via `uv python install 3.12`).
- Backend: `fastapi>=0.115`, `uvicorn[standard]>=0.32`, `sqlmodel>=0.0.22`, `sse-starlette>=2.1`, `huey>=2.5`, `pydantic>=2.9`, `httpx>=0.27`, `tenacity>=8.2`.
- Observability: `python-json-logger>=2.0`, `prometheus-client>=0.20`.
- Auth (extra `auth`): `python-jose[cryptography]>=3.3` (JWKS verification).

## Optional extras (pyproject `[project.optional-dependencies]`)
- `trainer` — `mlx>=0.31.2` + `mlx-lm>=0.31.3` (Darwin/arm64 markers); `transformers>=4.46`, `datasets>=3.1`, `huggingface-hub>=0.26`, `safetensors`.
- `trainer-cuda` — `torch>=2.4`, `peft>=0.13`, `trl>=0.12`, `bitsandbytes>=0.44; sys_platform=='linux'`, `accelerate>=1.0`.
- `exporter` — `torch>=2.0`, `gguf>=0.6`, `sentencepiece`.
- `ingest` — `playwright>=1.48`, `beautifulsoup4`, `boto3`, `trafilatura`.
- `chat` — `langgraph>=0.2.50`, `langchain-core>=0.3.20`, `langchain-ollama>=0.2.0`, `langgraph-checkpoint-sqlite>=2.0.1`.
- `research` — `ddgs>=8.0` (DuckDuckGo). Alt: `SERPAPI_KEY` / `TAVILY_API_KEY` env-only.
- `mcp` — `mcp>=1.0`.
- `error-responder` — `claude-agent-sdk>=0.2.106`.

## Python tooling
- Lint: `ruff>=0.7` — `line-length = 100`, `target-version = "py312"`, select `E,F,I,N,UP,B,A,C4,SIM,RUF`, ignore `E501`. Per-file: tests ignore `S101`.
- Types: `mypy>=1.13`, `strict = true`, `ignore_missing_imports = true`.
- Tests: `pytest>=8.3`, `pytest-asyncio>=0.24`, `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

## Frontend (`apps/web/`, npm)
- React 19, react-router-dom 7.1, recharts 2.15, `oidc-client-ts` 3.5.
- Vite 6, TypeScript 5.7, Tailwind 3.4.
- Build: `tsc --noEmit && vite build` — typecheck is the build gate.

## External services
- Ollama on host `:11434` for Hermes (`qwen3:30b-a3b`) — required by ratchet and chat.
- Keycloak (`:8080`) + OPA (`:8181`) — Phase M auth.
- llama.cpp tooling (`llama-quantize`, `convert_hf_to_gguf.py`) — required by exporter; brew on Mac or built from `scripts/llama_cpp_src/`.
- Grafana `:3001`, Prometheus `:9090`, Loki `:3100`, Promtail.
- LiteLLM proxy `:4000` — Anthropic-compatible front door over host Ollama (autofix profile).

## DB / storage
- SQLite via SQLModel. Schema bootstrap via `create_all`; additive migrations in `apps/api/services/db.py`.
- Runs / exports / adapters land on disk under `runs/`, `exports/`, `data/datasets/` — but **DB is the source of truth** (paths referenced from DB rows).
