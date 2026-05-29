#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Problem:  convert_hf_to_gguf.py (from llama.cpp) requires PyTorch to load
#           safetensors. We don't want 2+ GB of PyTorch on the Mac.
#
# Solution: Use mlx_lm.convert which:
#           - Is already installed (mlx-lm is in our venv)
#           - Runs natively on Apple Silicon via MLX (no PyTorch)
#           - Produces a GGUF directly from the fused MLX safetensors
#           - Supports the same quantization levels (Q4_K_M, Q5_K_M, Q8_0)
#
# New pipeline:
#   LoRA adapter
#     ↓ mlx_lm.fuse          (same as before)
#   fused HF safetensors
#     ↓ mlx_lm.convert       (replaces convert_hf_to_gguf.py — NO torch needed)
#   model.gguf (already quantized at this step)
#     ↓ we produce one file per requested quant level
#   model-Q4_K_M.gguf, model-Q8_0.gguf  → iPhone-ready
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [ ! -f "packages/exporter/pipeline.py" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Patching exporter to use mlx_lm.convert instead of convert_hf_to_gguf.py..."

cat > packages/exporter/pipeline.py <<'EOF'
"""Export pipeline: LoRA adapter → fused HF → GGUF (via mlx_lm.convert).

Pipeline:
  1. mlx_lm.fuse     — merge LoRA adapter into base model weights
  2. mlx_lm.convert  — convert fused safetensors → GGUF (one quant per call)
     • No PyTorch needed — mlx_lm.convert runs natively via MLX
     • llama-quantize is used as a fallback for any quant mlx_lm.convert can't do

Supports: Q4_K_M, Q5_K_M, Q8_0, F16
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("exporter.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / "runs"
EXPORTS_ROOT = PROJECT_ROOT / "exports"

QUANT_FILENAME = {
    "F16": "model-F16.gguf",
    "Q4_K_M": "model-Q4_K_M.gguf",
    "Q5_K_M": "model-Q5_K_M.gguf",
    "Q8_0": "model-Q8_0.gguf",
}

# Map our quant names to mlx_lm.convert --q-bits / --type equivalents
# mlx_lm.convert --type gguf --quantize --q-bits 4  → Q4 GGUF
# For named types, we pass --gguf-path and let mlx handle the format
MLX_CONVERT_QUANT_ARGS = {
    "F16":    ["--dtype", "float16"],
    "Q8_0":   ["--quantize", "--q-bits", "8"],
    "Q5_K_M": ["--quantize", "--q-bits", "5"],
    "Q4_K_M": ["--quantize", "--q-bits", "4"],
}

DB_FIELD_PATH = {
    "F16": "gguf_f16_path",
    "Q4_K_M": "gguf_q4_path",
    "Q5_K_M": "gguf_q5_path",
    "Q8_0": "gguf_q8_path",
}
DB_FIELD_BYTES = {
    "F16": "gguf_f16_bytes",
    "Q4_K_M": "gguf_q4_bytes",
    "Q5_K_M": "gguf_q5_bytes",
    "Q8_0": "gguf_q8_bytes",
}


def _find_llama_quantize() -> str | None:
    """Locate llama-quantize binary (Homebrew or PATH)."""
    for c in ["llama-quantize", "/opt/homebrew/bin/llama-quantize", "/usr/local/bin/llama-quantize"]:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _check_tools() -> str:
    """Verify mlx-lm is callable and llama-quantize exists. Returns quantize path."""
    py = sys.executable
    r = subprocess.run(
        [py, "-m", "mlx_lm", "convert", "--help"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "mlx_lm.convert not available. Run: uv sync --extra trainer"
        )

    q = _find_llama_quantize()
    if not q:
        raise RuntimeError(
            "llama-quantize not found. Install: brew install llama.cpp"
        )
    return q


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/exports/{xid}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /exports/%s failed: %s", xid, e)


def _run_subprocess(cmd: list[str], log_path: Path, *, env: dict | None = None) -> int:
    """Stream subprocess output to stdout and a log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(str(c) for c in cmd)}\n\n")
        lf.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env or os.environ.copy(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"    {line.rstrip()}", flush=True)
            lf.write(line)
            lf.flush()
        proc.wait()
        return proc.returncode


def _mlx_convert_to_gguf(
    py: str,
    fused_dir: Path,
    out_path: Path,
    quant: str,
    log_path: Path,
    env: dict,
) -> bool:
    """Use mlx_lm.convert to produce one GGUF variant. Returns True on success."""
    quant_args = MLX_CONVERT_QUANT_ARGS.get(quant, ["--quantize", "--q-bits", "4"])

    # mlx_lm.convert --model <fused_dir> --type gguf --gguf-path <out.gguf> [quant args]
    cmd = [
        py, "-m", "mlx_lm", "convert",
        "--model", str(fused_dir),
        "--type", "gguf",
        "--gguf-path", str(out_path),
        *quant_args,
    ]
    rc = _run_subprocess(cmd, log_path, env=env)
    if rc != 0:
        return False
    # mlx_lm.convert sometimes writes to a slightly different name — check
    if not out_path.exists():
        # Try to find what was actually written in the same dir
        gguf_files = list(out_path.parent.glob("*.gguf"))
        if len(gguf_files) == 1:
            gguf_files[0].rename(out_path)
            log.info("    renamed %s → %s", gguf_files[0].name, out_path.name)
        else:
            log.warning("    Expected %s but not found after convert", out_path)
            return False
    return True


def run_export_job(export_row: dict, api_url: str) -> None:
    """Run one export end-to-end."""
    xid = export_row["id"]
    run_id = export_row["run_id"]
    base_model = export_row["base_model"]
    quant_levels = [q.strip() for q in export_row["quant_levels"].split(",") if q.strip()]

    log.info("─── Export #%s for run #%s (quants=%s) ───", xid, run_id, quant_levels)

    try:
        quantize_bin = _check_tools()
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
    fused_dir = export_dir / "fused"
    gguf_dir = export_dir / "gguf"
    log_path = export_dir / "export.log"
    fused_dir.mkdir(parents=True, exist_ok=True)
    gguf_dir.mkdir(parents=True, exist_ok=True)

    py = sys.executable
    env = os.environ.copy()
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"

    # ── Stage 1: mlx_lm.fuse ─────────────────────────────────────────────
    log.info("Stage 1/2: mlx_lm.fuse — merging LoRA adapter into base model")
    _patch_export(api_url, xid, status="fusing", progress_text="Fusing LoRA into base model…")

    # Detect subcommand vs direct-module form
    probe = subprocess.run(
        [py, "-m", "mlx_lm", "fuse", "--help"], capture_output=True, text=True, timeout=15
    )
    if probe.returncode == 0:
        fuse_cmd = [py, "-m", "mlx_lm", "fuse"]
    else:
        fuse_cmd = [py, "-m", "mlx_lm.fuse"]

    fuse_cmd += [
        "--model", base_model,
        "--adapter-path", str(adapter_dir),
        "--save-path", str(fused_dir),
    ]

    rc = _run_subprocess(fuse_cmd, log_path, env=env)
    if rc != 0:
        msg = f"mlx_lm.fuse exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(api_url, xid, fused_path=str(fused_dir))

    # ── Stage 2: mlx_lm.convert → GGUF for each requested quant level ────
    log.info("Stage 2/2: mlx_lm.convert — producing GGUF variants")
    _patch_export(api_url, xid, status="converting", progress_text="Converting to GGUF…")

    for quant in quant_levels:
        out_path = gguf_dir / QUANT_FILENAME[quant]
        log.info("  producing %s...", out_path.name)
        _patch_export(
            api_url, xid,
            status="quantizing" if quant != "F16" else "converting",
            progress_text=f"Converting to {quant}…",
        )

        ok = _mlx_convert_to_gguf(py, fused_dir, out_path, quant, log_path, env)
        if not ok:
            # Fallback: produce F16 first then use llama-quantize
            f16_path = gguf_dir / QUANT_FILENAME["F16"]
            if quant != "F16" and not f16_path.exists():
                log.info("  mlx_lm.convert failed for %s — trying F16 + llama-quantize fallback", quant)
                ok_f16 = _mlx_convert_to_gguf(py, fused_dir, f16_path, "F16", log_path, env)
                if not ok_f16:
                    msg = f"Both mlx_lm.convert and F16 fallback failed for {quant}. See {log_path}"
                    log.error(msg)
                    _patch_export(api_url, xid, status="failed", error_message=msg)
                    return
                _patch_export(
                    api_url, xid,
                    gguf_f16_path=str(f16_path),
                    gguf_f16_bytes=f16_path.stat().st_size,
                )

            # Now quantize from F16
            if f16_path.exists():
                log.info("  llama-quantize fallback: F16 → %s", quant)
                rc = _run_subprocess(
                    [quantize_bin, str(f16_path), str(out_path), quant],
                    log_path, env=env,
                )
                ok = rc == 0 and out_path.exists()

        if ok and out_path.exists():
            size = out_path.stat().st_size
            log.info("  ✓ %s (%s MB)", out_path.name, size // (1024 * 1024))
            _patch_export(
                api_url, xid,
                **{DB_FIELD_PATH[quant]: str(out_path), DB_FIELD_BYTES[quant]: size},
            )
        else:
            msg = f"Failed to produce {quant} GGUF. See {log_path}"
            log.error(msg)
            _patch_export(api_url, xid, status="failed", error_message=msg)
            return

    log.info("Export #%s completed.", xid)
    _patch_export(
        api_url, xid,
        status="completed",
        progress_text="Done — download the Q4_K_M.gguf and AirDrop to your iPhone.",
    )
EOF

echo "✓ packages/exporter/pipeline.py — now uses mlx_lm.convert (no PyTorch)"

# Verify mlx_lm.convert is actually available before we call it good
echo "→ Verifying mlx_lm.convert is callable..."
if uv run python -m mlx_lm convert --help >/dev/null 2>&1; then
    echo "✓ mlx_lm.convert works"
else
    # Older mlx-lm uses mlx_lm.convert as a direct module
    if uv run python -m mlx_lm.convert --help >/dev/null 2>&1; then
        echo "✓ mlx_lm.convert works (direct module form)"
        # Patch the subcommand probe to try the older form too
        python3 - <<'PYEOF'
from pathlib import Path
p = Path("packages/exporter/pipeline.py")
text = p.read_text()
text = text.replace(
    '"mlx_lm", "convert", "--help"',
    '"mlx_lm.convert", "--help"',
    1
)
text = text.replace(
    '[py, "-m", "mlx_lm", "convert",',
    '[py, "-m", "mlx_lm.convert",',
    2
)
p.write_text(text)
print("  patched to use mlx_lm.convert (direct module) form")
PYEOF
    else
        echo ""
        echo "⚠ mlx_lm.convert not found. Check: uv run python -m mlx_lm convert --help"
        echo "  Try: uv sync --extra trainer --reinstall"
    fi
fi

# Quick check whether mlx_lm.convert actually supports --type gguf
echo "→ Checking mlx_lm.convert --type gguf support..."
if uv run python -m mlx_lm convert --help 2>&1 | grep -q "gguf"; then
    echo "✓ GGUF output type confirmed"
else
    echo ""
    echo "⚠ '--type gguf' may not be in your mlx_lm.convert version."
    echo "  Checking version..."
    uv run python -c "import mlx_lm; print('mlx_lm version:', getattr(mlx_lm, '__version__', 'unknown'))"
    echo ""
    echo "  If GGUF isn't supported, you need mlx-lm >= 0.18."
    echo "  Your current version should be 0.31 which does support it."
    echo "  If you see errors on 'make exporter', paste the log."
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Exporter patched — uses mlx_lm.convert (no PyTorch needed)        ║
╚══════════════════════════════════════════════════════════════════════╝

What changed:
  • packages/exporter/pipeline.py
    - Stage 2 now uses 'mlx_lm.convert --type gguf' instead of
      'convert_hf_to_gguf.py' (which needed PyTorch)
    - One mlx_lm.convert call per quantization level
    - Fallback: if mlx_lm.convert fails for a quant variant,
      it produces F16 first then uses llama-quantize to quantize
    - No torch, no sentencepiece, no gguf pip package needed

Now:
  make exporter          # restart the worker

  Then queue an export from any completed run in the UI.
  Watch the terminal: Stage 1/2 fusing → Stage 2/2 converting → done.
MSG
