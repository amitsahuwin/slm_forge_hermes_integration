#!/usr/bin/env bash
# Phase 1+: launches trainer worker, Hermes agent, exporter on host (not Docker).
# Reason: MLX/Metal is only available on host macOS, not inside Linux containers.
set -euo pipefail
echo "⚠  Host services launch is Phase 1+. Currently in Phase 0."
echo "   For Phase 0, 'make dev' (docker-compose) is all you need."
