#!/usr/bin/env bash
# Download the default Phase 1 base model from Hugging Face.
# Qwen2.5-3B-Instruct-4bit is ~1.9 GB.
set -euo pipefail

MODEL="${1:-mlx-community/Qwen2.5-3B-Instruct-4bit}"

echo "→ Downloading $MODEL to ~/.cache/huggingface ..."

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
echo "Done. mlx-lm will resolve '$MODEL' from this cache."
