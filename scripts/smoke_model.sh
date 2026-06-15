#!/usr/bin/env bash
# Phase P / Phase T — smoke-test one catalog model on this host.
#
# Runs a tiny LoRA job against a throwaway dataset and reports wall time,
# peak memory, and final train loss. Cross-platform:
#   • macOS (Apple Silicon) → MLX backend  (`mlx_lm lora`, `/usr/bin/time -l`)
#   • Linux  (NVIDIA GPU)   → CUDA backend  (PEFT/TRL, `/usr/bin/time -v`)
#
# Usage:
#   ./scripts/smoke_model.sh <catalog-key|model-id> [iters]
#   make smoke-model MODEL=gemma-4-e4b-it
#
# Requires: `uv sync --all-extras` done; HF login for gated repos
# (`huggingface-cli login` / HF_TOKEN).
set -euo pipefail

MODEL_ARG="${1:?usage: smoke_model.sh <catalog-key|model-id> [iters]}"
ITERS="${2:-30}"
API_URL="${SLM_FORGE_API_URL:-http://localhost:8000}"

OS="$(uname -s)"
# Backend follows the explicit env if set, else the host: MLX on macOS, CUDA elsewhere.
if [ -n "${SLM_FORGE_TRAINER_BACKEND:-}" ]; then
  BACKEND="$SLM_FORGE_TRAINER_BACKEND"
elif [ "$OS" = "Darwin" ]; then
  BACKEND="mlx"
else
  BACKEND="cuda"
fi

# Resolve a catalog key to the backend-appropriate checkpoint id via the API;
# fall back to treating the argument as a literal model id.
MODEL_ID="$MODEL_ARG"
if [[ "$MODEL_ARG" != */* ]]; then
  resolved="$(curl -sf "$API_URL/api/v1/models/v2" \
    | BACKEND="$BACKEND" python3 -c "
import json, os, sys
key = '$MODEL_ARG'; backend = os.environ['BACKEND']
for m in json.load(sys.stdin):
    if m['key'] == key:
        v = m.get('backends', {}).get(backend)
        print(v['model_id'] if v else ''); break
" || true)"
  if [[ -z "$resolved" ]]; then
    echo "ERROR: catalog key '$MODEL_ARG' has no '$BACKEND' variant via $API_URL/api/v1/models/v2" >&2
    echo "       (is 'make dev' running? or pass a full model id)" >&2
    exit 1
  fi
  MODEL_ID="$resolved"
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/slm_smoke.XXXXXX")"
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

# Choose a peak-memory probe: `/usr/bin/time -l` on macOS, `-v` on GNU/Linux.
TIME_BIN="/usr/bin/time"
if [ "$OS" = "Darwin" ]; then TIME_FLAG="-l"; else TIME_FLAG="-v"; fi
if [ ! -x "$TIME_BIN" ]; then TIME_BIN=""; TIME_FLAG=""; fi   # degrade gracefully

echo "── Smoke test ────────────────────────────────────────────────"
echo "backend: $BACKEND   host: $OS $(uname -m)"
echo "model:   $MODEL_ID"
echo "iters:   $ITERS   batch: 1   seq: 1024   grad-ckpt: on"
echo "──────────────────────────────────────────────────────────────"

LOG="$WORK/train.log"
START=$(date +%s)

if [ "$BACKEND" = "mlx" ]; then
  $TIME_BIN $TIME_FLAG uv run python -m mlx_lm lora \
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
else
  # CUDA: reuse the real CudaBackend to materialize a valid config + argv,
  # so the smoke path can never drift from the production trainer.
  CMD="$(MODEL_ID="$MODEL_ID" DATA="$DATA" ADAPTER="$ADAPTER" ITERS="$ITERS" \
    uv run python - <<'PY'
import os, shlex
from pathlib import Path
from packages.trainer.backends.cuda import CudaBackend
run = {
    "id": 0, "base_model": os.environ["MODEL_ID"], "method": "lora",
    "batch_size": 1, "iters": int(os.environ["ITERS"]),
    "learning_rate": 1e-4, "max_seq_length": 1024,
    "grad_checkpoint": True, "seed": 0,
}
b = CudaBackend()
cfg = b.write_config(run, Path(os.environ["DATA"]), Path(os.environ["ADAPTER"]))
cmd = b.build_command(cfg)
print(shlex.join(cmd) if cmd else "")
PY
  )"
  if [ -z "$CMD" ]; then
    echo "ERROR: CUDA toolchain not available. Run: uv sync --extra trainer-cuda" >&2
    exit 1
  fi
  # shellcheck disable=SC2086
  $TIME_BIN $TIME_FLAG $CMD 2>&1 | tee "$LOG"
fi

ELAPSED=$(( $(date +%s) - START ))

# Peak memory: macOS reports bytes ("maximum resident set size"); GNU time
# reports KB ("Maximum resident set size").
if [ "$OS" = "Darwin" ]; then
  PEAK_RAW="$(grep -E "maximum resident set size" "$LOG" | awk '{print $1}' | tail -1)"
  PEAK_GB="$(python3 -c "print(f'{${PEAK_RAW:-0}/1024**3:.1f}')")"
else
  PEAK_RAW="$(grep -E "Maximum resident set size" "$LOG" | awk -F': ' '{print $2}' | tail -1)"
  PEAK_GB="$(python3 -c "print(f'{${PEAK_RAW:-0}/1024**2:.1f}')")"
fi
FINAL_LOSS="$(grep -Eo "[Tt]rain loss [0-9.]+" "$LOG" | tail -1 | awk '{print $3}')"

echo ""
echo "── Result ────────────────────────────────────────────────────"
echo "backend:          $BACKEND"
echo "model:            $MODEL_ID"
echo "elapsed:          ${ELAPSED}s for $ITERS iters"
echo "peak RSS:         ${PEAK_GB} GB"
echo "last train loss:  ${FINAL_LOSS:-n/a}"
echo "──────────────────────────────────────────────────────────────"
echo "Record this in docs/MULTI_PLATFORM_TRAINING.md §2 and update"
echo "status/min_memory_gb in the model catalog."
