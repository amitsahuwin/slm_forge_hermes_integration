.PHONY: help setup install-hermes hermes-install-skills dev down build rebuild logs \
        trainer ratchet exporter check-llamacpp \
        seed-data download-base-model train-sample clean ensure-lock ensure-trainer-installed

help: ## Show this help
	@echo "SLM-Forge — local-first SLM fine-tuning lab"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'

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

trainer: ensure-trainer-installed ## Run the host trainer worker
	uv run python -m packages.trainer

ratchet: ## Run the autoresearch ratchet worker
	@if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then \
		echo "✗ Ollama not reachable at :11434"; exit 1; \
	fi
	uv run python -m packages.ratchet

exporter: ensure-trainer-installed check-llamacpp ## Run the GGUF export worker
	@echo "→ Starting exporter worker..."
	uv run python -m packages.exporter

ensure-lock:
	@if [ ! -f uv.lock ] || [ ! -f apps/web/package-lock.json ]; then \
		$(MAKE) setup; \
	fi

dev: ensure-lock ## Start UI + API
	docker compose up

rebuild: ensure-lock ## Force-rebuild Docker images
	docker compose down
	docker compose build --no-cache

down: ## Stop dev stack
	docker compose down

build: ensure-lock
	docker compose build

logs:
	docker compose logs -f

clean:
	rm -rf .venv apps/web/node_modules apps/web/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
