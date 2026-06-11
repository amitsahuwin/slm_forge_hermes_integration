# SLM-Forge — local-first SLM fine-tuning lab
#
# `make help` lists every target with a short description. Targets are
# grouped by purpose (core, workers, observability, auth, mcp, datasets,
# maintenance).
#
# Every worker target exports SLM_FORGE_LOG_FORMAT=json so all on-disk logs
# are structured for Loki/Grafana correlation.

# ─── Shared env injected into every worker process ─────────────────────────
export SLM_FORGE_LOG_FORMAT  ?= json
export SLM_FORGE_API_URL     ?= http://localhost:8000

# Service-account shared secret used by host workers (trainer/ratchet/exporter)
# to authenticate to the API when SLM_FORGE_AUTH_ENABLED=true. Pulled from the
# same .env that docker-compose reads, so API + workers always agree.
# To regenerate: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
include .env
export SLM_FORGE_SERVICE_TOKEN ?= dev-service-token-change-me-in-prod

# Compose files
COMPOSE      := docker compose
OBS_FILES    := -f docker-compose.yml -f docker-compose.observability.yml

.PHONY: help \
        setup install-hermes hermes-install-skills \
        dev rebuild down build logs ps \
        trainer ratchet exporter \
        obs-up obs-down obs-logs \
        auth auth-up auth-down auth-token \
        mcp-up mcp-down mcp-logs \
        admin-panel grafana keycloak-ui prometheus loki-explore \
        seed-data download-base-model synth-list research-list \
        opa-test check-llamacpp ensure-lock ensure-trainer-installed \
        clean nuke

# ─── Help ──────────────────────────────────────────────────────────────────

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@echo "USAGE:  make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "PROFILED targets (set extra env on the command line):"
	@echo "  make auth ENABLED=true    Bring up Keycloak+OPA AND turn enforcement ON."
	@echo "  make auth ENABLED=false   Bring up Keycloak+OPA, enforcement OFF (default)."
	@echo "  make auth-down            Tear down Keycloak+OPA."

# ─── One-time setup ────────────────────────────────────────────────────────

setup: ## Install all deps (Python via uv, Node via npm)
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	uv sync --all-extras
	cd apps/web && npm install
	@if uv run python -c "import mlx_lm" 2>/dev/null; then echo "✓ mlx-lm installed."; else echo "✗ mlx-lm did NOT install."; fi

install-hermes: ## Install Ollama + Hermes Agent + qwen3:30b-a3b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download the default base model from HF
	bash scripts/download_base_model.sh

ensure-trainer-installed:
	@if ! uv run python -c "import mlx_lm" 2>/dev/null; then \
		echo "✗ mlx-lm not installed. Run: uv sync --all-extras"; exit 1; \
	fi
	@if ! uv run python -m mlx_lm lora --help >/dev/null 2>&1; then \
		if ! uv run python -m mlx_lm.lora --help >/dev/null 2>&1; then \
			echo "✗ mlx-lm installed but module form fails. Run: uv sync --all-extras --refresh"; exit 1; \
		fi; \
	fi

check-llamacpp: ## Verify llama.cpp + convert_hf_to_gguf.py are available
	@if ! command -v llama-quantize >/dev/null 2>&1 && ! [ -x /opt/homebrew/bin/llama-quantize ]; then \
		echo "✗ llama-quantize not found. Install: brew install llama.cpp"; exit 1; \
	fi
	@echo "✓ llama-quantize found"
	@if [ -f scripts/llama_cpp/convert_hf_to_gguf.py ]; then \
		echo "✓ convert_hf_to_gguf.py found (scripts/llama_cpp/)"; \
	elif find /opt/homebrew -name convert_hf_to_gguf.py 2>/dev/null | grep -q .; then \
		echo "✓ convert_hf_to_gguf.py found (homebrew)"; \
	else \
		echo "✗ convert_hf_to_gguf.py not found."; \
		echo "  Run: chmod +x patch_llamacpp_convert.sh && ./patch_llamacpp_convert.sh"; \
		exit 1; \
	fi

ensure-lock:
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		$(MAKE) setup; \
	fi

# ─── Workers (run on host, in three separate terminals) ────────────────────
# Each worker exports SLM_FORGE_LOG_FORMAT=json so Promtail can parse fields.

trainer: ensure-trainer-installed ## Run the host trainer worker (T1)
	@echo "→ Trainer worker — JSON logs to runs/_trainer.log.json"
	SLM_FORGE_LOG_FORMAT=json uv run python -m packages.trainer

ratchet: ## Run the autoresearch ratchet worker (T2)
	@if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then \
		echo "✗ Ollama not reachable at :11434"; exit 1; \
	fi
	@echo "→ Ratchet worker — JSON logs to runs/_ratchet.log.json"
	SLM_FORGE_LOG_FORMAT=json uv run python -m packages.ratchet

exporter: ensure-trainer-installed check-llamacpp ## Run the GGUF export worker (T3)
	@echo "→ Exporter worker — JSON logs to runs/_exporter.log.json"
	SLM_FORGE_LOG_FORMAT=json uv run python -m packages.exporter

# ─── Core stack (UI + API) ─────────────────────────────────────────────────

dev: ensure-lock ## Start core UI + API (use `make dev-d` for detached)
	$(COMPOSE) up

dev-d: ensure-lock ## Start core UI + API detached
	$(COMPOSE) up -d

rebuild: ensure-lock ## Force-rebuild Docker images
	$(COMPOSE) down
	$(COMPOSE) build --no-cache

down: ## Stop core dev stack
	$(COMPOSE) down

build: ensure-lock ## Build Docker images
	$(COMPOSE) build

logs: ## Tail all core container logs
	$(COMPOSE) logs -f

ps: ## List running containers
	$(COMPOSE) ps

# ─── Observability stack (Loki + Promtail + Prometheus + Grafana) ──────────

obs-up: ## Bring up the observability overlay (Loki/Grafana/Prometheus/Promtail)
	@echo "→ Starting observability stack alongside core…"
	$(COMPOSE) $(OBS_FILES) up -d
	@echo ""
	@echo "  ✓ Grafana    → http://localhost:3001  (admin/admin)"
	@echo "  ✓ Prometheus → http://localhost:9090"
	@echo "  ✓ Loki       → http://localhost:3100"
	@echo ""
	@echo "Tip: run \`make trainer\` (etc.) so JSON logs flow into Loki."

obs-down: ## Stop the observability overlay (keeps core running)
	$(COMPOSE) $(OBS_FILES) down

obs-logs: ## Tail observability container logs
	$(COMPOSE) $(OBS_FILES) logs -f loki promtail prometheus grafana

grafana: ## Open Grafana in your browser
	@open http://localhost:3001 || xdg-open http://localhost:3001 || echo "Grafana → http://localhost:3001"

prometheus: ## Open Prometheus in your browser
	@open http://localhost:9090 || xdg-open http://localhost:9090 || echo "Prometheus → http://localhost:9090"

loki-explore: ## Open Grafana Explore (Loki datasource)
	@open "http://localhost:3001/explore?left=%7B%22datasource%22:%22Loki%22%7D" \
	  || echo "Open Grafana → Explore → pick Loki"

# ─── Authentication (Keycloak + OPA) ───────────────────────────────────────
# Use:  make auth ENABLED=true     — bring up stack AND turn enforcement ON
#       make auth ENABLED=false    — bring up stack with enforcement OFF (default)
#       make auth-down             — stop Keycloak + OPA
#       make auth-token            — print a fresh access token for admin@local

ENABLED ?= false

auth: ## Bring up Keycloak+OPA. Pass ENABLED=true|false to flip enforcement.
	@if [ "$(ENABLED)" = "true" ]; then \
	  echo "→ Auth ENABLED — JWT required for every protected endpoint."; \
	  SLM_FORGE_AUTH_ENABLED=true $(COMPOSE) --profile auth up -d; \
	elif [ "$(ENABLED)" = "false" ]; then \
	  echo "→ Auth services UP but enforcement OFF — every request gets the synthetic admin."; \
	  SLM_FORGE_AUTH_ENABLED=false $(COMPOSE) --profile auth up -d; \
	else \
	  echo "✗ ENABLED must be 'true' or 'false' (got: $(ENABLED))"; exit 1; \
	fi
	@echo ""
	@echo "  ✓ Keycloak admin console → http://localhost:8080  (admin / admin)"
	@echo "  ✓ OPA REPL               → http://localhost:8181"
	@echo "  Seed users:"
	@echo "    admin@local    / admin1234  → role: admin"
	@echo "    engineer@local / engineer   → role: data_engineer"
	@echo ""
	@echo "  Sign in at the SLM-Forge UI → click the user badge top-right."

auth-up: ## Alias for `make auth ENABLED=false`
	$(MAKE) auth ENABLED=false

auth-down: ## Stop Keycloak + OPA (core stack stays up)
	$(COMPOSE) --profile auth down

auth-token: ## Print a fresh JWT for admin@local (handy for curl testing)
	@curl -s -X POST "http://localhost:8080/realms/slm-forge/protocol/openid-connect/token" \
	  -d "grant_type=password" \
	  -d "client_id=slm-forge-web" \
	  -d "username=admin@local" \
	  -d "password=admin1234" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])"

opa-test: ## Run the OPA policy unit tests
	@command -v opa >/dev/null 2>&1 && opa test policies/ \
	  || docker run --rm -v "$$PWD/policies:/policies" openpolicyagent/opa:latest test /policies

# ─── MCP server (Claude Desktop / Cursor / Claude Code) ────────────────────

mcp-up: ## Start the MCP server (HTTP transport on :8765)
	$(COMPOSE) --profile mcp up -d
	@echo ""
	@echo "  ✓ MCP HTTP transport → http://localhost:8765"
	@echo "  Wire into Claude Desktop / Cursor / Claude Code — see docs/MCP_SETUP.md"

mcp-down: ## Stop the MCP server
	$(COMPOSE) --profile mcp down

mcp-logs: ## Tail MCP container logs
	$(COMPOSE) logs -f mcp

# ─── Convenience entry-points for the UI ──────────────────────────────────

admin-panel: ## Open the SLM-Forge admin panel (requires auth ENABLED + admin role)
	@open http://localhost:5173/admin/users || xdg-open http://localhost:5173/admin/users \
	  || echo "Admin panel → http://localhost:5173/admin/users"

synth-list: ## List currently-running synth jobs via the API
	@curl -s $(SLM_FORGE_API_URL)/api/v1/synth/jobs | python3 -m json.tool

research-list: ## List saved market-research reports via the API
	@curl -s $(SLM_FORGE_API_URL)/api/v1/research/reports | python3 -m json.tool

# ─── Cleanup ──────────────────────────────────────────────────────────────

clean: ## Remove build caches / node_modules / .venv
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

nuke: clean ## Also stop every compose stack and wipe Docker volumes
	$(COMPOSE) $(OBS_FILES) --profile auth --profile mcp down -v
	@echo "All stacks down + volumes wiped."
