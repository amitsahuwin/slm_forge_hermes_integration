#!/usr/bin/env bash
# Download the default Phase 1 base model from Hugging Face.
# Qwen2.5-3B-Instruct-4bit is ~1.9 GB.
set -euo pipefail

MODEL="${1:-mlx-community/Qwen2.5-3B-Instruct-4bit}"

echo "→ Downloading $MODEL to ~/.cache/huggingface ..."

if ! command -v uv &>/dev/null; then
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "✗ uv not found. Install: brew install uv"
    else
        echo "✗ uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
    exit 1
fi

uv run python - <<PYEOF
from huggingface_hub import snapshot_download
path = snapshot_download(repo_id="$MODEL")
print(f"✓ Cached at: {path}")
PYEOF

echo ""
echo "Done. The trainer (mlx-lm or transformers/PEFT) will resolve '$MODEL' from this cache."
echo "Tip: for the CUDA backend pass a full-precision HF id, e.g."
echo "     scripts/download_base_model.sh Qwen/Qwen2.5-3B-Instruct"
