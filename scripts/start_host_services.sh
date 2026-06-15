#!/usr/bin/env bash
# Phase 1+: launches the trainer / ratchet / exporter workers on the host
# (not in Docker).
#
# Why on the host: the training backends use hardware accelerators that are
# not available inside the core Linux containers — MLX/Metal on Apple Silicon,
# or the NVIDIA CUDA stack on a Linux GPU host. Run each worker in its own
# terminal alongside `make dev`.
set -euo pipefail

OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
    BACKEND_HINT="MLX (Apple Silicon / Metal)"
else
    BACKEND_HINT="CUDA (NVIDIA GPU)"
fi

cat <<MSG
Host workers — detected backend: $BACKEND_HINT

Start the core stack first, then each worker in its own terminal:

  make dev          # UI + API (docker-compose)
  make trainer      # training worker (auto-selects backend for this host)
  make ratchet      # autoresearch worker (needs Ollama — see make install-hermes)
  make exporter     # GGUF export worker (needs llama.cpp — see make check-llamacpp)

Override the trainer backend explicitly with:  make trainer TRAINER_BACKEND=mlx|cuda
MSG
