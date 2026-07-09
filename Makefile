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

# ─── Host detection (Phase T — cross-platform) ─────────────────────────────
# The same targets work on macOS (Apple Silicon → MLX) and Linux (NVIDIA →
# CUDA). Detection happens once, at parse time. Override any of these on the
# command line, e.g. `make trainer TRAINER_BACKEND=mlx`.
UNAME_S      := $(shell uname -s)
UNAME_M      := $(shell uname -m)
HAS_NVIDIA   := $(shell command -v nvidia-smi >/dev/null 2>&1 && echo 1 || echo 0)

# Export platform vars for docker-compose (so the API container knows the host platform)
export SLM_FORGE_PLATFORM_OS := $(UNAME_S)
export SLM_FORGE_PLATFORM_ARCH := $(UNAME_M)
export SLM_FORGE_PLATFORM_HAS_NVIDIA := $(if $(filter 1,$(HAS_NVIDIA)),true,false)

ifeq ($(UNAME_S),Darwin)
  PLATFORM          := mac
  TRAINER_BACKEND   ?= mlx
  UV_INSTALL_HINT   := brew install uv
  NODE_INSTALL_HINT := brew install node
else
  PLATFORM          := linux
  TRAINER_BACKEND   ?= cuda
  UV_INSTALL_HINT   := curl -LsSf https://astral.sh/uv/install.sh | sh
  NODE_INSTALL_HINT := sudo apt-get install -y nodejs npm   (or https://nodejs.org)
endif

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
        smoke-model trainer-cuda trainer-mlx platform-info \
        wipe-clean \
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
	@echo "  make smoke-model MODEL=gemma-4-e4b-it   Smoke-test a catalog model on this host."
	@echo "  SLM_FORGE_WIPE_CONFIRM=YES make wipe-clean   Phase D clean-slate (destroys all runs/sessions/exports/chats/traces)."
	@echo ""
	@echo "Detected host: $(PLATFORM) ($(UNAME_S)/$(UNAME_M)) · GPU=$(if $(filter 1,$(HAS_NVIDIA)),NVIDIA,none) · backend=$(TRAINER_BACKEND)"

platform-info: ## Print the detected platform + chosen trainer backend
	@echo "OS:              $(UNAME_S)"
	@echo "Arch:            $(UNAME_M)"
	@echo "Platform:        $(PLATFORM)"
	@echo "NVIDIA GPU:      $(if $(filter 1,$(HAS_NVIDIA)),yes,no)"
	@echo "Trainer backend: $(TRAINER_BACKEND)  (override: make trainer TRAINER_BACKEND=mlx|cuda)"

# ─── One-time setup ────────────────────────────────────────────────────────

setup: ## Install all deps (Python via uv, Node via npm) — auto-detects platform
	@command -v uv >/dev/null 2>&1 || { echo "✗ uv not found. Install: $(UV_INSTALL_HINT)"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "✗ node not found. Install: $(NODE_INSTALL_HINT)"; exit 1; }
	@echo "→ Host: $(PLATFORM) ($(UNAME_S)/$(UNAME_M)) · backend=$(TRAINER_BACKEND)"
	@echo "→ Ensuring a managed Python 3.12 (project floor; system Python may be older)…"
	uv python install 3.12 || echo "  ⚠ 'uv python install 3.12' failed; uv sync will still try to resolve a 3.12."
	uv sync --all-extras
	cd apps/web && npm install
	@# Platform markers (pyproject) skip MLX off Apple Silicon and bitsandbytes off Linux,
	@# so we verify the toolchain that actually matters for THIS host's backend.
	@if [ "$(TRAINER_BACKEND)" = "mlx" ]; then \
	  if uv run python -c "import mlx_lm" 2>/dev/null; then echo "✓ mlx-lm installed (mlx backend ready)."; \
	  else echo "✗ mlx-lm did NOT install (expected on Apple Silicon)."; fi; \
	else \
	  if uv run python -c "import torch, peft" 2>/dev/null; then echo "✓ torch + peft installed (cuda backend ready)."; \
	  else echo "✗ torch/peft did NOT install. Run: uv sync --extra trainer-cuda"; fi; \
	fi

install-hermes: ## Install Ollama + Hermes Agent + qwen3:30b-a3b
	bash scripts/install_hermes.sh

hermes-install-skills: ## Copy .hermes-skills/* into ~/.hermes/skills/
	bash scripts/install_skills.sh

seed-data: ## Copy bundled sample datasets into data/datasets/
	uv run python scripts/seed_datasets.py

download-base-model: ## Download the default base model from HF
	bash scripts/download_base_model.sh

ensure-trainer-installed:
	@# Backend-aware toolchain check (Phase T): probe MLX on Apple Silicon,
	@# torch+peft on CUDA hosts.
	@if [ "$(TRAINER_BACKEND)" = "mlx" ]; then \
		if ! uv run python -c "import mlx_lm" 2>/dev/null; then \
			echo "✗ mlx-lm not installed. Run: uv sync --all-extras"; exit 1; \
		fi; \
		if ! uv run python -m mlx_lm lora --help >/dev/null 2>&1; then \
			if ! uv run python -m mlx_lm.lora --help >/dev/null 2>&1; then \
				echo "✗ mlx-lm installed but module form fails. Run: uv sync --all-extras --refresh"; exit 1; \
			fi; \
		fi; \
	else \
		if ! uv run python -c "import torch, peft, trl" 2>/dev/null; then \
			echo "✗ CUDA trainer deps missing. Run: uv sync --extra trainer-cuda"; exit 1; \
		fi; \
	fi

check-llamacpp: ## Verify llama.cpp + convert_hf_to_gguf.py are available
	@if command -v llama-quantize >/dev/null 2>&1 \
	   || [ -x scripts/llama_cpp_src/build/bin/llama-quantize ] \
	   || [ -x /opt/homebrew/bin/llama-quantize ] \
	   || [ -x /usr/local/bin/llama-quantize ] \
	   || [ -x /usr/bin/llama-quantize ]; then \
		echo "✓ llama-quantize found"; \
	elif [ "$(PLATFORM)" = "mac" ]; then \
		echo "✗ llama-quantize not found. Install: brew install llama.cpp"; exit 1; \
	else \
		echo "✗ llama-quantize not found. Install via apt/conda, or build the bundled clone:"; \
		echo "    cmake -S scripts/llama_cpp_src -B scripts/llama_cpp_src/build"; \
		echo "    cmake --build scripts/llama_cpp_src/build -j --target llama-quantize"; \
		exit 1; \
	fi
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

trainer: ensure-trainer-installed ## Run the host trainer worker (auto-detects backend)
	@echo "→ Trainer worker [$(TRAINER_BACKEND)] — JSON logs to runs/_trainer.log.json"
	SLM_FORGE_TRAINER_BACKEND=$(TRAINER_BACKEND) SLM_FORGE_LOG_FORMAT=json uv run python -m packages.trainer

trainer-mlx: ## Force the MLX trainer worker (Apple Silicon)
	@echo "→ MLX trainer worker — JSON logs to runs/_trainer.log.json"
	SLM_FORGE_TRAINER_BACKEND=mlx SLM_FORGE_LOG_FORMAT=json uv run python -m packages.trainer

trainer-cuda: ## Force the CUDA trainer worker (Linux + NVIDIA only)
	@echo "→ CUDA trainer worker — requires .[trainer-cuda] extras; HF_TOKEN is auto-loaded from .env for gated models (accept the license once on the HF model page)"
	SLM_FORGE_TRAINER_BACKEND=cuda SLM_FORGE_LOG_FORMAT=json uv run python -m packages.trainer

smoke-model: ensure-trainer-installed ## Smoke-test a catalog model (MODEL=<key>, e.g. gemma-4-e4b-it)
	@test -n "$(MODEL)" || { echo "usage: make smoke-model MODEL=<catalog-key>"; exit 1; }
	uv run bash scripts/smoke_model.sh "$(MODEL)" $(or $(ITERS),30)

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

obs-up: ## Bring up the observability overlay (Loki/Grafana/Prometheus/Promtail/cAdvisor)
	@echo "→ Starting observability stack alongside core…"
	$(COMPOSE) $(OBS_FILES) up -d
	@echo ""
	@echo "  ✓ Grafana    → http://localhost:3001  (admin/admin)"
	@echo "  ✓ Prometheus → http://localhost:9090"
	@echo "  ✓ cAdvisor   → http://localhost:8085"
	@echo "  ✓ Loki       → http://localhost:3100"
	@echo ""
	@echo "Tip: run \`make trainer\` (etc.) so JSON logs flow into Loki."

obs-down: ## Stop the observability overlay (keeps core running)
	$(COMPOSE) $(OBS_FILES) down

obs-logs: ## Tail observability container logs
	$(COMPOSE) $(OBS_FILES) logs -f loki promtail prometheus grafana cadvisor

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
	@echo "  Seed users — one per role (see docs/AUTH.md for the capability matrix):"
	@echo "    admin@local    / admin1234  → admin          (full access)"
	@echo "    engineer@local / engineer   → data_engineer  (datasets + experiments)"
	@echo "    expert@local   / expert123  → domain_expert  (read + research RW)"
	@echo "    devops@local   / devops123  → devops         (logs + settings)"
	@echo "    ops@local      / ops12345   → operations     (read + execute exports)"
	@echo "    support@local  / support1   → support        (read-only)"
	@echo ""
	@echo "  Multi-tenant demo users (tenant isolation — Phase C):"
	@echo "    alice@acme     / alice1234  → admin          (tenant: acme)"
	@echo "    bob@acme       / bob12345   → data_engineer  (tenant: acme)"
	@echo "    viewer@acme    / viewer12   → viewer         (tenant: acme)"
	@echo "    carol@globex   / carol123   → admin          (tenant: globex)"
	@echo "    dave@globex    / dave1234   → data_engineer  (tenant: globex)"
	@echo "    viewer@globex  / viewer34   → viewer         (tenant: globex)"
	@echo ""
	@echo "  Sign in at the SLM-Forge UI → click the user badge top-right."

auth-up: ## Alias for `make auth ENABLED=false`
	$(MAKE) auth ENABLED=false

auth-down: ## Stop Keycloak + OPA (core stack stays up)
	$(COMPOSE) --profile auth down

auth-token: ## Print a fresh JWT. EMAIL=<user> PASSWORD=<pw> override defaults (admin@local / admin1234). Phase C demo users: alice@acme/alice1234, bob@acme/bob12345, carol@globex/carol123, dave@globex/dave1234, viewer@acme/viewer12, viewer@globex/viewer34
	@EMAIL="$${EMAIL:-admin@local}"; \
	 PASSWORD="$${PASSWORD:-$$( [ "$$EMAIL" = "admin@local" ] && echo admin1234 || \
	   [ "$$EMAIL" = "alice@acme" ] && echo alice1234 || \
	   [ "$$EMAIL" = "bob@acme" ] && echo bob12345 || \
	   [ "$$EMAIL" = "carol@globex" ] && echo carol123 || \
	   [ "$$EMAIL" = "dave@globex" ] && echo dave1234 || \
	   [ "$$EMAIL" = "viewer@acme" ] && echo viewer12 || \
	   [ "$$EMAIL" = "viewer@globex" ] && echo viewer34 )}"; \
	 curl -s -X POST "http://localhost:8080/realms/slm-forge/protocol/openid-connect/token" \
	  -d "grant_type=password" \
	  -d "client_id=slm-forge-web" \
	  -d "username=$$EMAIL" \
	  -d "password=$$PASSWORD" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])"

auth-worker-token: ## Print a fresh worker JWT (client_credentials, Phase C R4)
	@curl -s -X POST "http://localhost:8080/realms/slm-forge/protocol/openid-connect/token" \
	  -d "grant_type=client_credentials" \
	  -d "client_id=$${SLM_FORGE_WORKER_CLIENT_ID:-slm-forge-worker}" \
	  -d "client_secret=$${SLM_FORGE_WORKER_CLIENT_SECRET:-slm-forge-worker-dev-secret}" \
	  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])"

opa-test: ## Run the OPA policy unit tests
	@command -v opa >/dev/null 2>&1 && opa test policies/ \
	  || docker run --rm -v "$$PWD/policies:/policies" openpolicyagent/opa:latest test /policies

# ─── Apache Ozone object storage (Phase D) ─────────────────────────────────
#
# Default storage backend is ``s3`` (Ozone) when SLM_FORGE_STORAGE=s3 (the
# default in .env.example). Set SLM_FORGE_STORAGE=local to skip these
# targets entirely and stay on the legacy filesystem.

OZONE_CLUSTER ?= slm-forge-ozone
OZONE_NS      ?= slm-forge-ozone
OZONE_RELEASE ?= ozone
OZONE_CHART   ?= apache-ozone/ozone
OZONE_S3_ENDPOINT ?= http://localhost:9878
OZONE_CONTROL_PLANE := $(OZONE_CLUSTER)-control-plane

ozone-heal-network: ## Repair kubelet.conf if a Docker/Desktop restart changed the kind node's IP (idempotent; safe to run every time)
	@# Docker Desktop doesn't guarantee the kind control-plane container keeps
	@# the same bridge IP across a daemon/VM restart (e.g. after a laptop
	@# reboot). kubeadm bakes that IP as a literal into kubelet.conf once, at
	@# cluster-creation time, and nothing re-templates it afterwards — so if
	@# the container comes back up with a new IP, kubelet dials the old
	@# (now-dead) address, the node goes NotReady, and `kubectl port-forward`
	@# fails with "SPDY upgrade failed: 401 Unauthorized" because the
	@# apiserver can no longer tunnel to a healthy kubelet. admin.conf already
	@# addresses the control-plane by its Docker DNS hostname (which self-
	@# updates via /etc/hosts on every boot and is already a trusted SAN on
	@# the apiserver cert) — repointing kubelet.conf at that same hostname
	@# fixes it permanently, with no repair needed on future restarts.
	@if [ -n "$$(docker ps -q -f name=^/$(OZONE_CONTROL_PLANE)$$ -f status=running 2>/dev/null)" ]; then \
	  if docker exec $(OZONE_CONTROL_PLANE) sh -c "grep -qE 'server: https://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:6443' /etc/kubernetes/kubelet.conf" 2>/dev/null; then \
	    echo "→ kubelet.conf on $(OZONE_CONTROL_PLANE) pins a raw container IP that drifted after a Docker restart; repointing at the stable hostname..."; \
	    docker exec $(OZONE_CONTROL_PLANE) sh -c "sed -i -E 's#server: https://[0-9.]+:6443#server: https://$(OZONE_CONTROL_PLANE):6443#' /etc/kubernetes/kubelet.conf && systemctl restart kubelet"; \
	    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
	      kubectl get node $(OZONE_CONTROL_PLANE) --no-headers 2>/dev/null | awk '{print $$2}' | grep -q '^Ready$$' && break; \
	      sleep 1; \
	    done; \
	    echo "  ✓ node repaired and Ready."; \
	  fi; \
	fi

ozone-up: ## Create a kind cluster + helm-install Apache Ozone (Phase D)
	@command -v kind >/dev/null 2>&1 || { echo "kind not found — install: https://kind.sigs.k8s.io"; exit 1; }
	@command -v helm >/dev/null 2>&1 || { echo "helm not found — install: https://helm.sh/docs/intro/install/"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found — install: https://kubernetes.io/docs/tasks/tools/"; exit 1; }
	@if kind get clusters | grep -q '^$(OZONE_CLUSTER)$$'; then \
	  echo "→ kind cluster $(OZONE_CLUSTER) already exists; skipping create."; \
	else \
	  echo "→ Creating kind cluster $(OZONE_CLUSTER)..."; \
	  kind create cluster --config deploy/ozone/kind-config.yaml --name $(OZONE_CLUSTER); \
	fi
	@kubectl config use-context kind-$(OZONE_CLUSTER) >/dev/null
	@$(MAKE) --no-print-directory ozone-heal-network
	@helm repo add apache-ozone https://apache.github.io/ozone-helm-charts 2>/dev/null || true
	@helm repo update apache-ozone >/dev/null
	@if helm status -n $(OZONE_NS) $(OZONE_RELEASE) >/dev/null 2>&1; then \
	  echo "→ Helm release $(OZONE_RELEASE) already present; upgrading."; \
	  helm upgrade $(OZONE_RELEASE) $(OZONE_CHART) -f deploy/ozone/values.yaml -n $(OZONE_NS); \
	else \
	  echo "→ Installing $(OZONE_CHART) in namespace $(OZONE_NS)..."; \
	  helm install $(OZONE_RELEASE) $(OZONE_CHART) -f deploy/ozone/values.yaml -n $(OZONE_NS) --create-namespace; \
	fi
	@echo ""
	@echo "  ✓ Ozone installing. Watch readiness with: make ozone-status"
	@echo "  S3 gateway will be reachable at $(OZONE_S3_ENDPOINT) once pods are Running."
	@echo "  After all pods are Ready, run: make ozone-bootstrap"

ozone-down: ## Uninstall Ozone + delete the kind cluster
	@if helm status -n $(OZONE_NS) $(OZONE_RELEASE) >/dev/null 2>&1; then \
	  helm uninstall $(OZONE_RELEASE) -n $(OZONE_NS); \
	fi
	@kubectl delete ns $(OZONE_NS) --ignore-not-found
	@if kind get clusters | grep -q '^$(OZONE_CLUSTER)$$'; then \
	  echo "→ Deleting kind cluster $(OZONE_CLUSTER)..."; \
	  kind delete cluster --name $(OZONE_CLUSTER); \
	else \
	  echo "→ kind cluster $(OZONE_CLUSTER) not found; nothing to delete."; \
	fi

ozone-status: ## Show Ozone pods, PVCs, and an S3 ls of buckets
	@$(MAKE) --no-print-directory ozone-heal-network
	@echo "── kind clusters ───────────────────────────────────────"
	@kind get clusters | sed 's/^/  /'
	@echo ""
	@echo "── pods in $(OZONE_NS) ─────────────────────────────────"
	@kubectl get pods -n $(OZONE_NS) 2>/dev/null || echo "  (namespace missing — run make ozone-up)"
	@echo ""
	@echo "── PVCs ────────────────────────────────────────────────"
	@kubectl get pvc -n $(OZONE_NS) 2>/dev/null || true
	@echo ""
	@echo "── S3 buckets (probed via temporary port-forward) ──────"
	@# Ozone's S3 gateway is ClusterIP-only (chart default), so we port-
	@# forward briefly to probe it. Ozone doesn't implement
	@# ``list_buckets`` on ``/`` — scripts/ozone_status.py heads the
	@# four seeded tenant buckets instead.
	@if ! kubectl get svc -n $(OZONE_NS) ozone-s3g-rest >/dev/null 2>&1; then \
	  echo "  (ozone-s3g-rest service not found — Ozone may not be installed yet)"; \
	else \
	  kubectl port-forward -n $(OZONE_NS) svc/ozone-s3g-rest 9878:9878 \
	    >/tmp/slm-forge-ozone-status-pf.log 2>&1 & \
	  PF_PID=$$!; \
	  trap "kill $$PF_PID 2>/dev/null || true" EXIT INT TERM; \
	  for i in 1 2 3 4 5 6 7 8 9 10; do \
	    nc -z localhost 9878 >/dev/null 2>&1 && break; sleep 0.3; \
	  done; \
	  uv run python scripts/ozone_status.py 2>/dev/null || echo "  (probe failed)"; \
	  kill $$PF_PID 2>/dev/null || true; \
	fi

ozone-bootstrap: ## Create the slm-forge demo tenant buckets in Ozone
	@$(MAKE) --no-print-directory ozone-heal-network
	@# The S3 gateway service is ClusterIP-only (chart default), so we
	@# spin a short-lived background port-forward, run the bootstrap
	@# script, then tear the forward down. ``trap`` ensures cleanup
	@# even on script failure or Ctrl-C.
	@command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found"; exit 1; }
	@if ! kubectl get svc -n $(OZONE_NS) ozone-s3g-rest >/dev/null 2>&1; then \
	  echo "ozone-s3g-rest service not found in $(OZONE_NS); run 'make ozone-up' first."; \
	  exit 1; \
	fi
	@echo "→ Starting temporary port-forward to ozone-s3g-rest..."
	@set -e; \
	 kubectl port-forward -n $(OZONE_NS) svc/ozone-s3g-rest 9878:9878 >/tmp/slm-forge-ozone-pf.log 2>&1 & \
	 PF_PID=$$!; \
	 trap "kill $$PF_PID 2>/dev/null || true" EXIT; \
	 echo "  port-forward pid=$$PF_PID"; \
	 for i in 1 2 3 4 5 6 7 8 9 10; do \
	   if curl -sS -o /dev/null --max-time 1 http://localhost:9878/ 2>/dev/null; then break; fi; \
	   sleep 0.5; \
	 done; \
	 echo "→ Creating per-tenant S3 buckets..."; \
	 SLM_FORGE_OZONE_S3_ENDPOINT=http://localhost:9878 uv run python scripts/ozone_bootstrap.py
	@echo "  ✓ Done. Buckets persist in Ozone; port-forward closed."

ozone-port-forward: ## Port-forward the Ozone S3 gateway to localhost:9878 (foreground)
	@$(MAKE) --no-print-directory ozone-heal-network
	@if ! kubectl get svc -n $(OZONE_NS) ozone-s3g-rest >/dev/null 2>&1; then \
	  echo "ozone-s3g-rest service not found in $(OZONE_NS); run 'make ozone-up' first."; \
	  exit 1; \
	fi
	@echo "→ Forwarding ozone-s3g-rest:9878 → localhost:9878 (Ctrl-C to stop)..."
	@kubectl port-forward -n $(OZONE_NS) svc/ozone-s3g-rest 9878:9878

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

# ─── LiteLLM proxy (Anthropic-compatible front door over host Ollama) ─────
#
# Used by the auto-fix loop's claude_agent_sdk invocation so the SDK can
# call a local Ollama model instead of the real Anthropic API. Opt-in via
# the `autofix` profile; default `make dev` doesn't pull this image.
#
# Once running: anthropic-shaped POSTs to http://localhost:4000 (host) or
# http://litellm:4000 (intra-compose) are translated to Ollama calls
# against host.docker.internal:11434 — see litellm/config.yaml for the
# model alias table.

litellm-up: ## Start the LiteLLM proxy (port 4000) — requires Ollama running on host :11434
	$(COMPOSE) --profile autofix up -d litellm
	@echo ""
	@echo "  ✓ LiteLLM proxy → http://localhost:4000  (master key in litellm/config.yaml)"
	@echo "  Set in .env:  ANTHROPIC_BASE_URL=http://localhost:4000  AUTOFIX_MODEL=anthropic/claude-3-5-sonnet-20241022"

litellm-down: ## Stop the LiteLLM proxy (core stack stays up)
	$(COMPOSE) --profile autofix down

litellm-logs: ## Tail LiteLLM proxy logs
	$(COMPOSE) logs -f litellm

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
	$(COMPOSE) $(OBS_FILES) --profile auth --profile mcp --profile autofix down -v
	@echo "All stacks down + volumes wiped."

wipe-clean: ## Phase D clean-slate cutover. DESTROYS all runs/sessions/exports/chats/traces. Set SLM_FORGE_WIPE_CONFIRM=YES first.
	@if [ "$$SLM_FORGE_WIPE_CONFIRM" != "YES" ]; then \
	  echo "✗ Refusing to wipe. Run as: SLM_FORGE_WIPE_CONFIRM=YES make wipe-clean"; exit 2; \
	fi
	@echo "→ Phase D clean-slate cutover (see release/0.9.0.md for what gets wiped)..."
	uv run python scripts/wipe_clean.py
	@echo "✓ Wipe complete. Database and artifact roots reset; bundled sample datasets re-seeded under data/datasets/global/."
