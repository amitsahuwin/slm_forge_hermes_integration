.PHONY: help setup install-hermes hermes-install-skills dev down build rebuild logs trainer ratchet \
        seed-data download-base-model train-sample clean ensure-lock

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

setup: ## Install all deps (Python via uv, Node via npm)
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: brew install uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: brew install node"; exit 1; }
	uv sync --all-extras
	cd apps/web && npm install

install-hermes: ## Install Ollama + Hermes Agent + qwen2.5-coder:14b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download Gemma 3n E2B base model from HF (~1.5 GB)
	bash scripts/download_base_model.sh

trainer: ## Run the host trainer worker (Metal access)
	uv run python -m packages.trainer

ratchet: ## Run the autoresearch ratchet worker (needs trainer + Ollama)
	@echo "→ Starting autoresearch ratchet worker..."
	@echo "  Required: 'make dev', 'make trainer', and Ollama running."
	uv run python -m packages.ratchet

ensure-lock:
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		echo "→ Lock files missing — running 'make setup'..."; \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API (docker-compose, live reload)
	docker compose up

rebuild: ensure-lock ## Force-rebuild Docker images (use after editing package.json / pyproject.toml)
	docker compose down
	docker compose build --no-cache

down: ## Stop dev stack
	docker compose down

build: ensure-lock ## Build Docker images (incremental)
	docker compose build

logs:
	docker compose logs -f

clean:
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
