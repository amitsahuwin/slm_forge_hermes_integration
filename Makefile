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
