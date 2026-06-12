#!/usr/bin/env bash
# Phase P — smoke-test one catalog model on this Mac.
#
# Runs a tiny 30-iter LoRA job against a throwaway dataset and reports
# wall time, peak memory, and final train loss. Use the results to update
# the catalog's min_memory_gb/status (apps/api/services/model_catalog.py)
# and the matrix in docs/MULTI_PLATFORM_TRAINING.md §2.
#
# Usage:
#   ./scripts/smoke_model.sh <catalog-key|hf-model-id> [iters]
#   make smoke-model MODEL=gemma-4-e4b-it
#
# Requires: macOS (Metal), `uv sync --extra trainer` done, HF login for
# gated repos (huggingface-cli login).
set -euo pipefail

MODEL_ARG="${1:?usage: smoke_model.sh <catalog-key|hf-model-id> [iters]}"
ITERS="${2:-30}"
API_URL="${SLM_FORGE_API_URL:-http://localhost:8000}"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: MLX smoke tests must run on macOS (Metal)." >&2
  exit 1
fi

# Resolve a catalog key to its mlx checkpoint id via the API when possible;
# fall back to treating the argument as a literal HF id.
MODEL_ID="$MODEL_ARG"
if [[ "$MODEL_ARG" != */* ]]; then
  resolved="$(curl -sf "$API_URL/api/v1/models/v2" \
    | python3 -c "
import json, sys
key = '$MODEL_ARG'
for m in json.load(sys.stdin):
    if m['key'] == key:
        print(m['backends']['mlx']['model_id']); break
" || true)"
  if [[ -z "$resolved" ]]; then
    echo "ERROR: catalog key '$MODEL_ARG' not found via $API_URL/api/v1/models/v2" >&2
    echo "       (is 'make dev' running? or pass a full HF model id)" >&2
    exit 1
  fi
  MODEL_ID="$resolved"
fi

WORK="$(mktemp -d /tmp/slm_smoke.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
DATA="$WORK/data"; ADAPTER="$WORK/adapter"
mkdir -p "$DATA"

# Tiny chat dataset (enough rows for batch_size=1 + val).
python3 - "$DATA" <<'EOF'
import json, sys
from pathlib import Path
data = Path(sys.argv[1])
row = {"messages": [
    {"role": "user", "content": "What does SLM-Forge do?"},
    {"role": "assistant", "content": "It fine-tunes small language models locally."},
]}
line = json.dumps(row) + "\n"
(data / "train.jsonl").write_text(line * 16)
(data / "valid.jsonl").write_text(line * 4)
EOF

echo "── Smoke test ────────────────────────────────────────────────"
echo "model:  $MODEL_ID"
echo "iters:  $ITERS   batch: 1   layers: 8   seq: 1024   grad-ckpt: on"
echo "──────────────────────────────────────────────────────────────"

LOG="$WORK/train.log"
START=$(date +%s)
# /usr/bin/time -l reports "maximum resident set size" in bytes on macOS.
/usr/bin/time -l python -m mlx_lm lora \
  --model "$MODEL_ID" \
  --train \
  --data "$DATA" \
  --adapter-path "$ADAPTER" \
  --iters "$ITERS" \
  --batch-size 1 \
  --num-layers 8 \
  --max-seq-length 1024 \
  --grad-checkpoint \
  --steps-per-report 10 \
  2>&1 | tee "$LOG"
ELAPSED=$(( $(date +%s) - START ))

PEAK_BYTES="$(grep -E "maximum resident set size" "$LOG" | awk '{print $1}' | tail -1)"
PEAK_GB="$(python3 -c "print(f'{${PEAK_BYTES:-0}/1024**3:.1f}')")"
FINAL_LOSS="$(grep -Eo "Train loss [0-9.]+" "$LOG" | tail -1 | awk '{print $3}')"
TOKS="$(grep -Eo "Tokens/sec [0-9.]+" "$LOG" | tail -1 | awk '{print $2}')"

echo ""
echo "── Result ────────────────────────────────────────────────────"
echo "model:            $MODEL_ID"
echo "elapsed:          ${ELAPSED}s for $ITERS iters"
echo "peak RSS:         ${PEAK_GB} GB"
echo "last train loss:  ${FINAL_LOSS:-n/a}"
echo "tokens/sec:       ${TOKS:-n/a}"
echo "──────────────────────────────────────────────────────────────"
echo "Record this in docs/MULTI_PLATFORM_TRAINING.md §2 and update"
echo "status/min_memory_gb in apps/api/services/model_catalog.py."
