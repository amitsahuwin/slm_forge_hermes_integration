#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Root cause: convert_hf_to_gguf.py imports from a 'gguf-py' package
# that only exists in the llama.cpp source tree. Downloading the .py file
# alone is not enough — it needs the full source context to run.
#
# Fix: shallow-clone llama.cpp at the exact tag matching your Homebrew version
# (b9380), run convert_hf_to_gguf.py from within that clone so all imports
# resolve, then quantize with your existing Homebrew llama-quantize binary.
#
# Pipeline (final, correct):
#   1. mlx_lm.fuse --dequantize     → full-precision HF safetensors
#   2. llama.cpp/convert_hf_to_gguf.py  → F16 GGUF (run from source tree)
#   3. llama-quantize (Homebrew)     → Q4_K_M, Q8_0
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "packages/exporter" ]; then
    echo "✗ Run from project root."
    exit 1
fi

LLAMA_TAG="b9380"
LLAMA_CLONE_DIR="scripts/llama_cpp_src"

# ─────────────────────────────────────────────────────────────
# 1. Shallow-clone llama.cpp source at the matching tag
# ─────────────────────────────────────────────────────────────
if [ -d "$LLAMA_CLONE_DIR/.git" ]; then
    echo "✓ llama.cpp source already cloned at $LLAMA_CLONE_DIR"
else
    echo "→ Shallow-cloning llama.cpp at tag $LLAMA_TAG (~50 MB, Python files only)..."
    git clone \
        --depth 1 \
        --branch "$LLAMA_TAG" \
        --filter=blob:none \
        --sparse \
        https://github.com/ggml-org/llama.cpp.git \
        "$LLAMA_CLONE_DIR" 2>&1 | tail -5

    # We only need the Python conversion files
    cd "$LLAMA_CLONE_DIR"
    git sparse-checkout set convert_hf_to_gguf.py gguf-py
    cd - > /dev/null
    echo "✓ llama.cpp source cloned"
fi

CONVERT_SCRIPT="$(pwd)/$LLAMA_CLONE_DIR/convert_hf_to_gguf.py"
GGUF_PY_PATH="$(pwd)/$LLAMA_CLONE_DIR/gguf-py"

if [ ! -f "$CONVERT_SCRIPT" ]; then
    echo "✗ convert_hf_to_gguf.py not found at $CONVERT_SCRIPT"
    echo "  Try: rm -rf $LLAMA_CLONE_DIR and re-run this script"
    exit 1
fi
echo "✓ convert_hf_to_gguf.py: $CONVERT_SCRIPT"
echo "✓ gguf-py package: $GGUF_PY_PATH"

# ─────────────────────────────────────────────────────────────
# 2. Install Python deps for convert_hf_to_gguf.py
# ─────────────────────────────────────────────────────────────
echo "→ Installing convert_hf_to_gguf.py Python dependencies..."
# Install the gguf-py package from the source tree (authoritative version)
if [ -f "$GGUF_PY_PATH/pyproject.toml" ]; then
    uv pip install --quiet "$GGUF_PY_PATH"
    echo "✓ gguf (from llama.cpp source)"
elif [ -f "$GGUF_PY_PATH/setup.py" ]; then
    uv pip install --quiet "$GGUF_PY_PATH"
    echo "✓ gguf (from llama.cpp source)"
else
    uv pip install --quiet gguf
    echo "✓ gguf (from PyPI)"
fi
uv pip install --quiet torch sentencepiece transformers
echo "✓ torch, sentencepiece, transformers"

# ─────────────────────────────────────────────────────────────
# 3. Verify convert_hf_to_gguf.py runs without import errors
# ─────────────────────────────────────────────────────────────
echo "→ Verifying convert_hf_to_gguf.py imports..."
cd "$LLAMA_CLONE_DIR"
if uv run python convert_hf_to_gguf.py --help >/dev/null 2>&1; then
    echo "✓ convert_hf_to_gguf.py runs cleanly"
else
    echo "✗ Still failing. Error output:"
    uv run python convert_hf_to_gguf.py --help 2>&1 | head -20
    cd - > /dev/null
    exit 1
fi
cd - > /dev/null

# ─────────────────────────────────────────────────────────────
# 4. Write the definitive pipeline.py
# ─────────────────────────────────────────────────────────────
echo "→ Writing packages/exporter/pipeline.py..."

cat > packages/exporter/pipeline.py <<PYEOF
"""Export pipeline: LoRA adapter → fused HF → F16 GGUF → quantized GGUF.

Pipeline:
  1. mlx_lm fuse --dequantize     — merge LoRA + produce fp16 safetensors
  2. convert_hf_to_gguf.py        — HF safetensors → F16 GGUF
     (run from llama.cpp source tree so all imports resolve)
  3. llama-quantize (Homebrew)    — F16 GGUF → Q4_K_M / Q5_K_M / Q8_0
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

# llama.cpp source clone — convert_hf_to_gguf.py must be run from here
LLAMA_SRC = PROJECT_ROOT / "scripts" / "llama_cpp_src"
CONVERT_SCRIPT = LLAMA_SRC / "convert_hf_to_gguf.py"

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
    for c in ["llama-quantize", "/opt/homebrew/bin/llama-quantize", "/usr/local/bin/llama-quantize"]:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _check_tools() -> tuple[str, str]:
    """Returns (quantize_bin, convert_script_path). Raises RuntimeError if anything is missing."""
    q = _find_llama_quantize()
    if not q:
        raise RuntimeError("llama-quantize not found. Run: brew install llama.cpp")

    if not CONVERT_SCRIPT.exists():
        raise RuntimeError(
            f"convert_hf_to_gguf.py not found at {CONVERT_SCRIPT}.\n"
            "Run: ./patch_exporter_final.sh"
        )

    # Quick import check — run from the llama.cpp src dir so gguf package resolves
    r = subprocess.run(
        [sys.executable, str(CONVERT_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=20,
        cwd=str(LLAMA_SRC),
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"convert_hf_to_gguf.py failed import check:\n{r.stderr[:500]}\n"
            "Run: ./patch_exporter_final.sh"
        )

    return q, str(CONVERT_SCRIPT)


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/exports/{xid}", json=fields, timeout=10).raise_for_status()
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
    xid = export_row["id"]
    run_id = export_row["run_id"]
    base_model = export_row["base_model"]
    quant_levels = [q.strip() for q in export_row["quant_levels"].split(",") if q.strip()]

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

    py = sys.executable
    env = os.environ.copy()
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        env["PATH"] = f"{scripts_dir}{os.pathsep}{env.get('PATH', '')}"

    # ── Stage 1: mlx_lm fuse ─────────────────────────────────────────
    log.info("Stage 1/3: mlx_lm fuse --dequantize")
    _patch_export(api_url, xid, status="fusing",
                  progress_text="Fusing LoRA into base model (dequantize)…")

    # Detect subcommand vs direct-module form
    probe = subprocess.run(
        [py, "-m", "mlx_lm", "fuse", "--help"],
        capture_output=True, text=True, timeout=15,
    )
    fuse_base = [py, "-m", "mlx_lm", "fuse"] if probe.returncode == 0 \
                else [py, "-m", "mlx_lm.fuse"]

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
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]

    # CRITICAL: run from LLAMA_SRC so 'import gguf' resolves from gguf-py/
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
# 5. Write __main__.py (clean, aligned with new _check_tools)
# ─────────────────────────────────────────────────────────────
cat > packages/exporter/__main__.py <<'EOF'
"""Export worker — polls for queued exports and processes them."""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

try:
    from dotenv import load_dotenv
    from pathlib import Path as _P
    load_dotenv(_P(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from packages.exporter.pipeline import _check_tools, run_export_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("exporter.worker")

API_URL       = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/api/v1/exports",
                      params={"status": "queued", "limit": 1}, timeout=5)
        r.raise_for_status()
        rows = r.json()
        return rows[-1] if rows else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Exporter worker starting (API=%s)", API_URL)

    for attempt in range(30):
        try:
            httpx.get(f"{API_URL}/api/v1/health", timeout=2).raise_for_status()
            log.info("API is up.")
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                log.info("Waiting for API...")
            time.sleep(2)
    else:
        log.error("API never came up. Is 'make dev' running?")
        return 1

    try:
        quantize_bin, convert_script = _check_tools()
        log.info("✓ llama-quantize : %s", quantize_bin)
        log.info("✓ convert script : %s", convert_script)
    except RuntimeError as e:
        log.error("Pre-flight failed:\n%s", e)
        return 1

    log.info("Ready. Polling every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)
    export: dict | None = None

    while True:
        try:
            export = fetch_next_queued()
            if export is None:
                time.sleep(POLL_INTERVAL)
                continue
            log.info("Picked up export #%s (run=%s)", export["id"], export["run_id"])
            run_export_job(export, api_url=API_URL)
        except KeyboardInterrupt:
            log.info("Stopping.")
            return 0
        except Exception as e:  # noqa: BLE001
            log.exception("Unexpected error: %s", e)
            if export:
                try:
                    httpx.patch(f"{API_URL}/api/v1/exports/{export['id']}",
                                json={"status": "failed",
                                      "error_message": str(e)[:500]}, timeout=10)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
EOF

echo "✓ packages/exporter/__main__.py written"

# ─────────────────────────────────────────────────────────────
# 6. Update .gitignore for the source clone
# ─────────────────────────────────────────────────────────────
if ! grep -q "llama_cpp_src" .gitignore 2>/dev/null; then
    printf "\n# llama.cpp source clone (large; re-cloned by patch_exporter_final.sh)\nscripts/llama_cpp_src/\n" >> .gitignore
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Exporter — definitive fix applied                                 ║
╚══════════════════════════════════════════════════════════════════════╝

Root cause fixed:
  convert_hf_to_gguf.py imports 'gguf' from the gguf-py/ subpackage
  that only exists in the llama.cpp SOURCE tree. Downloading just the
  .py file is not enough. Now we shallow-clone the full source at b9380
  and run the script from within that directory (cwd=LLAMA_SRC) so
  all imports resolve correctly.

Pipeline:
  1. mlx_lm fuse --dequantize
  2. scripts/llama_cpp_src/convert_hf_to_gguf.py  (run from its own dir)
  3. llama-quantize (Homebrew)

Now:
  make exporter
  Then queue an export from /runs in the UI.
MSG
