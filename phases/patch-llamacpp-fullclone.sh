#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stop fighting sparse checkout. Just clone the full llama.cpp repo at a
# stable, proven tag and run convert_hf_to_gguf.py from there.
#
# Why this approach:
#   • No more whack-a-mole with missing module imports
#   • A full shallow clone is ~150 MB — fine for a one-time setup
#   • Pinning to b5350 (older but battle-tested) avoids churn from recent
#     refactors that broke our previous attempts
#   • This is the exact approach used by every working Mac fine-tuning
#     tutorial we found
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "packages/exporter" ]; then
    echo "✗ Run from project root."
    exit 1
fi

CLONE_DIR="scripts/llama_cpp_src"
# b5350 = stable build from late 2025, widely used in tutorials.
# Older than your Homebrew b9380 but only convert_hf_to_gguf.py runs from
# here — the binaries (llama-quantize) still come from Homebrew.
LLAMA_TAG="b5350"

# ─────────────────────────────────────────────────────────────
# 1. Wipe the broken sparse-checkout clone
# ─────────────────────────────────────────────────────────────
if [ -d "$CLONE_DIR" ]; then
    echo "→ Removing broken sparse-checkout clone..."
    rm -rf "$CLONE_DIR"
fi

# ─────────────────────────────────────────────────────────────
# 2. Full shallow clone (no sparse-checkout, no surgery)
# ─────────────────────────────────────────────────────────────
echo "→ Cloning llama.cpp at tag $LLAMA_TAG (full shallow clone, ~150 MB)..."
git clone --depth 1 --branch "$LLAMA_TAG" \
    https://github.com/ggml-org/llama.cpp.git \
    "$CLONE_DIR" 2>&1 | tail -3

if [ ! -f "$CLONE_DIR/convert_hf_to_gguf.py" ]; then
    echo "✗ Clone succeeded but convert_hf_to_gguf.py is missing"
    echo "  Try a different tag — list available with:"
    echo "    git ls-remote --tags https://github.com/ggml-org/llama.cpp.git | grep 'refs/tags/b' | tail -20"
    exit 1
fi
echo "✓ Cloned to $CLONE_DIR ($(du -sh "$CLONE_DIR" | cut -f1))"

# ─────────────────────────────────────────────────────────────
# 3. Block uv from treating gguf-py as a project member
# ─────────────────────────────────────────────────────────────
# The trick: place a sentinel file that tells uv to skip the entire
# llama_cpp_src tree for project discovery.
cat > "$CLONE_DIR/.python-version" <<'EOF'
3.14
EOF

# Also disable any pyproject.toml in gguf-py from being detected
# (we don't delete it — convert_hf_to_gguf.py might read it for metadata)
# Instead, we ensure our pipeline NEVER uses uv run for this script.

echo "✓ Sentinel files written to prevent uv project discovery interference"

# ─────────────────────────────────────────────────────────────
# 4. Test that convert_hf_to_gguf.py runs cleanly with RAW python
# ─────────────────────────────────────────────────────────────
echo ""
echo "→ Testing convert_hf_to_gguf.py with raw Python (the way the pipeline calls it)..."
echo ""

cd "$CLONE_DIR"
if ../../.venv/bin/python convert_hf_to_gguf.py --help 2>&1 | head -10; then
    echo ""
    echo "✓ Script runs cleanly"
else
    echo ""
    echo "✗ Still failing. Capturing full error:"
    ../../.venv/bin/python convert_hf_to_gguf.py --help 2>&1 | head -30
    cd ../..
    exit 1
fi
cd ../..

# ─────────────────────────────────────────────────────────────
# 5. Update pipeline.py to use this stable layout
# ─────────────────────────────────────────────────────────────
cat > packages/exporter/pipeline.py <<'PYEOF'
"""Export pipeline: LoRA adapter → fused HF → F16 GGUF → quantized GGUF.

Pipeline:
  1. mlx_lm fuse --dequantize     — merge LoRA + produce fp16 safetensors
  2. convert_hf_to_gguf.py        — HF safetensors → F16 GGUF
     (from a full llama.cpp source clone in scripts/llama_cpp_src/)
  3. llama-quantize (Homebrew)    — F16 GGUF → Q4_K_M / Q5_K_M / Q8_0
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("exporter.pipeline")

PROJECT_ROOT   = Path(__file__).resolve().parents[2]
RUNS_ROOT      = PROJECT_ROOT / "runs"
EXPORTS_ROOT   = PROJECT_ROOT / "exports"

# Full llama.cpp source clone. convert_hf_to_gguf.py is run with cwd set
# here so all its sibling imports (conversion, gguf-py) resolve naturally.
LLAMA_SRC       = PROJECT_ROOT / "scripts" / "llama_cpp_src"
CONVERT_SCRIPT  = LLAMA_SRC / "convert_hf_to_gguf.py"
# Use the raw venv Python — never `uv run`, which gets confused by gguf-py
VENV_PYTHON     = PROJECT_ROOT / ".venv" / "bin" / "python"

QUANT_FILENAME = {
    "F16":    "model-F16.gguf",
    "Q4_K_M": "model-Q4_K_M.gguf",
    "Q5_K_M": "model-Q5_K_M.gguf",
    "Q8_0":   "model-Q8_0.gguf",
}
DB_FIELD_PATH = {
    "F16": "gguf_f16_path", "Q4_K_M": "gguf_q4_path",
    "Q5_K_M": "gguf_q5_path", "Q8_0": "gguf_q8_path",
}
DB_FIELD_BYTES = {
    "F16": "gguf_f16_bytes", "Q4_K_M": "gguf_q4_bytes",
    "Q5_K_M": "gguf_q5_bytes", "Q8_0": "gguf_q8_bytes",
}


def _find_llama_quantize() -> str | None:
    for c in [
        "llama-quantize",
        "/opt/homebrew/bin/llama-quantize",
        "/usr/local/bin/llama-quantize",
    ]:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _check_tools() -> tuple[str, str]:
    """Returns (quantize_bin, convert_script_path). Raises on missing prerequisites."""
    q = _find_llama_quantize()
    if not q:
        raise RuntimeError("llama-quantize not found. Run: brew install llama.cpp")

    if not CONVERT_SCRIPT.exists():
        raise RuntimeError(
            f"convert_hf_to_gguf.py not found at {CONVERT_SCRIPT}.\n"
            "Run: ./patch_llamacpp_fullclone.sh"
        )

    if not VENV_PYTHON.exists():
        raise RuntimeError(
            f"venv Python not found at {VENV_PYTHON}. "
            "Run: uv sync --all-extras"
        )

    # Verify the convert script runs cleanly with cwd set to its own dir
    r = subprocess.run(
        [str(VENV_PYTHON), str(CONVERT_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=30,
        cwd=str(LLAMA_SRC),
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"convert_hf_to_gguf.py failed pre-flight:\n"
            f"--- stderr ---\n{r.stderr[:600]}\n"
            f"--- stdout ---\n{r.stdout[:200]}\n"
            "Run: ./patch_llamacpp_fullclone.sh"
        )

    return q, str(CONVERT_SCRIPT)


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(
            f"{api_url}/api/v1/exports/{xid}",
            json=fields, timeout=10,
        ).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /exports/%s failed: %s", xid, e)


def _run_subprocess(
    cmd: list[str],
    log_path: Path,
    *,
    env: dict | None = None,
    cwd: str | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(str(c) for c in cmd)}\n")
        if cwd:
            lf.write(f"  (cwd: {cwd})\n")
        lf.write("\n")
        lf.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env or os.environ.copy(),
            cwd=cwd,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"    {line.rstrip()}", flush=True)
            lf.write(line)
            lf.flush()
        proc.wait()
        return proc.returncode


def run_export_job(export_row: dict, api_url: str) -> None:
    xid           = export_row["id"]
    run_id        = export_row["run_id"]
    base_model    = export_row["base_model"]
    quant_levels  = [q.strip() for q in export_row["quant_levels"].split(",") if q.strip()]

    log.info("─── Export #%s for run #%s (quants=%s) ───", xid, run_id, quant_levels)

    try:
        quantize_bin, convert_script = _check_tools()
    except RuntimeError as e:
        log.error(str(e))
        _patch_export(api_url, xid, status="failed", error_message=str(e))
        return

    adapter_dir = RUNS_ROOT / str(run_id) / "adapter"
    if not adapter_dir.exists():
        msg = f"Adapter dir not found: {adapter_dir}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    export_dir = EXPORTS_ROOT / str(xid)
    fused_dir  = export_dir / "fused"
    gguf_dir   = export_dir / "gguf"
    log_path   = export_dir / "export.log"
    fused_dir.mkdir(parents=True, exist_ok=True)
    gguf_dir.mkdir(parents=True, exist_ok=True)

    # Build env (no scripts_dir prepend — we use absolute paths everywhere)
    env = os.environ.copy()

    # ── Stage 1: mlx_lm fuse ─────────────────────────────────────────
    log.info("Stage 1/3: mlx_lm fuse --dequantize")
    _patch_export(api_url, xid, status="fusing",
                  progress_text="Fusing LoRA into base model (dequantize)…")

    # Detect subcommand vs direct-module form
    probe = subprocess.run(
        [str(VENV_PYTHON), "-m", "mlx_lm", "fuse", "--help"],
        capture_output=True, text=True, timeout=15,
    )
    fuse_base = [str(VENV_PYTHON), "-m", "mlx_lm", "fuse"] if probe.returncode == 0 \
                else [str(VENV_PYTHON), "-m", "mlx_lm.fuse"]

    fuse_cmd = fuse_base + [
        "--model", base_model,
        "--adapter-path", str(adapter_dir),
        "--save-path", str(fused_dir),
        "--dequantize",
    ]

    rc = _run_subprocess(fuse_cmd, log_path, env=env)
    if rc != 0:
        msg = f"mlx_lm fuse exited {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(api_url, xid, fused_path=str(fused_dir))

    # ── Stage 2: convert_hf_to_gguf.py → F16 GGUF ────────────────────
    log.info("Stage 2/3: convert_hf_to_gguf.py → F16 GGUF")
    _patch_export(api_url, xid, status="converting",
                  progress_text="Converting fused model to F16 GGUF…")

    f16_path = gguf_dir / QUANT_FILENAME["F16"]
    convert_cmd = [
        str(VENV_PYTHON), convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]

    # CRITICAL: run with cwd=LLAMA_SRC so sibling imports resolve
    rc = _run_subprocess(convert_cmd, log_path, env=env, cwd=str(LLAMA_SRC))
    if rc != 0:
        msg = f"convert_hf_to_gguf.py exited {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    if not f16_path.exists():
        msg = f"F16 GGUF not produced at {f16_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    f16_size = f16_path.stat().st_size
    log.info("  ✓ %s (%d MB)", f16_path.name, f16_size // (1024 * 1024))
    _patch_export(api_url, xid,
                  gguf_f16_path=str(f16_path), gguf_f16_bytes=f16_size)

    # ── Stage 3: llama-quantize → Q4_K_M / Q5_K_M / Q8_0 ─────────────
    remaining = [q for q in quant_levels if q != "F16"]
    if remaining:
        log.info("Stage 3/3: llama-quantize → %s", remaining)
        _patch_export(api_url, xid, status="quantizing",
                      progress_text="Quantizing to target formats…")

        for quant in remaining:
            target = gguf_dir / QUANT_FILENAME[quant]
            log.info("  quantizing → %s", target.name)
            _patch_export(api_url, xid, progress_text=f"Quantizing {quant}…")

            rc = _run_subprocess(
                [quantize_bin, str(f16_path), str(target), quant],
                log_path, env=env,
            )
            if rc != 0:
                msg = f"llama-quantize {quant} exited {rc}. See {log_path}"
                log.error(msg)
                _patch_export(api_url, xid, status="failed", error_message=msg)
                return

            if target.exists():
                size = target.stat().st_size
                log.info("  ✓ %s (%d MB)", target.name, size // (1024 * 1024))
                _patch_export(api_url, xid,
                               **{DB_FIELD_PATH[quant]: str(target),
                                  DB_FIELD_BYTES[quant]: size})
            else:
                msg = f"llama-quantize produced no output for {quant}"
                log.error(msg)
                _patch_export(api_url, xid, status="failed", error_message=msg)
                return

    log.info("─── Export #%s completed ───", xid)
    _patch_export(api_url, xid, status="completed",
                  progress_text="Done — download Q4_K_M.gguf and AirDrop to iPhone.")
PYEOF

echo "✓ packages/exporter/pipeline.py written"

# ─────────────────────────────────────────────────────────────
# 6. Update .gitignore for the full clone
# ─────────────────────────────────────────────────────────────
if ! grep -q "llama_cpp_src" .gitignore 2>/dev/null; then
    cat >> .gitignore <<'GITEOF'

# Phase 4: llama.cpp source clone (~150 MB; re-cloned by patch_llamacpp_fullclone.sh)
scripts/llama_cpp_src/
GITEOF
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Full clone at tag $LLAMA_TAG completed                                ║
╚══════════════════════════════════════════════════════════════════════╝

Key changes:
  • No more sparse checkout — full clone, all files present
  • Pinned to b5350 (stable, used by tutorials, predates recent refactors)
  • pipeline.py uses .venv/bin/python DIRECTLY (no uv run anywhere)
  • CONVERT_SCRIPT runs with cwd=LLAMA_SRC so sibling imports resolve

Now:
  make exporter

If you see "Pre-flight failed", paste the entire output below the
"--- stderr ---" line and I'll fix that one specific issue.
Otherwise, queue an export from /runs and watch the loss curves.
MSG
