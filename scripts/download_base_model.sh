#!/usr/bin/env bash
# Download the default Phase 1 base model from Hugging Face (~1.5 GB, one-time).
set -euo pipefail

MODEL="${1:-mlx-community/gemma-3n-E2B-it-bf16}"

echo "→ Downloading $MODEL to your local HF cache (~/.cache/huggingface)..."

if ! command -v uv &>/dev/null; then
    echo "✗ uv not found. Install: brew install uv"
    exit 1
fi

uv run python - <<PYEOF
from huggingface_hub import snapshot_download
path = snapshot_download(repo_id="$MODEL")
print(f"✓ Cached at: {path}")
PYEOF

echo ""
echo "Done. mlx_lm.lora will resolve '$MODEL' from this cache from now on."
