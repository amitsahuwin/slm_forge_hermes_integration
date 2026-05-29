#!/usr/bin/env bash
# Reality check: GGUF conversion needs PyTorch to load safetensors.
# On Apple Silicon, torch is ~60-70MB (no CUDA), so it's fine.
#
# Pipeline (final, correct):
#   1. mlx_lm.fuse           → fused HF safetensors
#   2. convert_hf_to_gguf.py → F16 GGUF  (needs torch + gguf)
#   3. llama-quantize         → Q4_K_M / Q5_K_M / Q8_0
set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "packages/exporter" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Installing torch + gguf Python packages..."
uv pip install torch gguf sentencepiece
echo "✓ torch installed"

# Verify convert script exists
if [ ! -f "scripts/llama_cpp/convert_hf_to_gguf.py" ]; then
    echo "→ Downloading convert_hf_to_gguf.py..."
    mkdir -p scripts/llama_cpp
    curl -fsSL "https://raw.githubusercontent.com/ggml-org/llama.cpp/b9380/convert_hf_to_gguf.py" \
        -o scripts/llama_cpp/convert_hf_to_gguf.py
fi
echo "✓ convert_hf_to_gguf.py present"

echo "→ Rewriting packages/exporter/pipeline.py..."

cat > packages/exporter/pipeline.py <<'EOF'
"""Export pipeline: LoRA adapter → fused HF → F16 GGUF → quantized GGUF.

Pipeline:
  1. mlx_lm.fuse            — merge LoRA adapter into base model (MLX-native)
  2. convert_hf_to_gguf.py  — HF safetensors → F16 GGUF (needs torch)
  3. llama-quantize          — F16 GGUF → Q4_K_M / Q5_K_M / Q8_0
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
    for c in ["llama-quantize", "/opt/homebrew/bin/llama-quantize", "/usr/local/bin/llama-quantize"]:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _find_convert_script() -> str | None:
    # Project-local copy first
    local = PROJECT_ROOT / "scripts" / "llama_cpp" / "convert_hf_to_gguf.py"
    if local.exists():
        return str(local)
    # Homebrew paths
    import glob
    for pattern in [
        "/opt/homebrew/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/Cellar/llama.cpp/*/share/llama.cpp/convert_hf_to_gguf.py",
    ]:
        for p in glob.glob(pattern):
            if os.path.exists(p):
                return p
    return None


def _check_tools() -> tuple[str, str]:
    """Returns (quantize_bin, convert_script). Raises RuntimeError if either missing."""
    q = _find_llama_quantize()
    if not q:
        raise RuntimeError("llama-quantize not found. Install: brew install llama.cpp")

    c = _find_convert_script()
    if not c:
        raise RuntimeError(
            "convert_hf_to_gguf.py not found. Run: ./patch_llamacpp_convert.sh "
            "or manually download it to scripts/llama_cpp/"
        )

    # Verify torch is importable
    py = sys.executable
    r = subprocess.run(
        [py, "-c", "import torch; print(torch.__version__)"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError("torch not installed. Run: uv pip install torch")

    return q, c


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/exports/{xid}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /exports/%s failed: %s", xid, e)


def _run_subprocess(cmd: list[str], log_path: Path, *, env: dict | None = None) -> int:
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


def run_export_job(export_row: dict, api_url: str) -> None:
    """Run one export: fuse → convert → quantize."""
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

    # ── Stage 1: mlx_lm.fuse ─────────────────────────────────────────
    log.info("Stage 1/3: mlx_lm.fuse — merging LoRA adapter into base model")
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
        "--de-quantize",  # ensure full-precision safetensors for clean GGUF conversion
    ]

    rc = _run_subprocess(fuse_cmd, log_path, env=env)
    if rc != 0:
        msg = f"mlx_lm.fuse exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(api_url, xid, fused_path=str(fused_dir))

    # ── Stage 2: convert_hf_to_gguf.py → F16 GGUF ────────────────────
    log.info("Stage 2/3: convert_hf_to_gguf.py — HF safetensors → F16 GGUF")
    _patch_export(api_url, xid, status="converting", progress_text="Converting to F16 GGUF…")

    f16_path = gguf_dir / QUANT_FILENAME["F16"]
    convert_cmd = [
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]
    rc = _run_subprocess(convert_cmd, log_path, env=env)
    if rc != 0:
        msg = f"convert_hf_to_gguf.py exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    if not f16_path.exists():
        msg = f"F16 GGUF not at expected path: {f16_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    f16_size = f16_path.stat().st_size
    log.info("  ✓ %s (%s MB)", f16_path.name, f16_size // (1024 * 1024))
    _patch_export(api_url, xid, gguf_f16_path=str(f16_path), gguf_f16_bytes=f16_size)

    # If F16 is the only requested level, we're done with conversion
    remaining = [q for q in quant_levels if q != "F16"]

    # ── Stage 3: llama-quantize → Q4_K_M / Q5_K_M / Q8_0 ─────────────
    if remaining:
        log.info("Stage 3/3: llama-quantize — F16 → quantized variants")
        _patch_export(api_url, xid, status="quantizing", progress_text="Quantizing…")

        for quant in remaining:
            target = gguf_dir / QUANT_FILENAME[quant]
            log.info("  quantizing F16 → %s...", target.name)
            _patch_export(api_url, xid, progress_text=f"Quantizing {quant}…")

            rc = _run_subprocess(
                [quantize_bin, str(f16_path), str(target), quant],
                log_path, env=env,
            )
            if rc != 0:
                msg = f"llama-quantize {quant} exited with code {rc}. See {log_path}"
                log.error(msg)
                _patch_export(api_url, xid, status="failed", error_message=msg)
                return

            if target.exists():
                size = target.stat().st_size
                log.info("  ✓ %s (%s MB)", target.name, size // (1024 * 1024))
                _patch_export(
                    api_url, xid,
                    **{DB_FIELD_PATH[quant]: str(target), DB_FIELD_BYTES[quant]: size},
                )
            else:
                msg = f"llama-quantize produced no output for {quant}"
                log.error(msg)
                _patch_export(api_url, xid, status="failed", error_message=msg)
                return

    log.info("─── Export #%s completed ───", xid)
    _patch_export(
        api_url, xid,
        status="completed",
        progress_text="Done — download Q4_K_M.gguf and AirDrop to your iPhone.",
    )
EOF

echo "✓ packages/exporter/pipeline.py — uses convert_hf_to_gguf.py + torch"

# Fix __main__.py to unpack (q, c) correctly now that _check_tools returns 2 values again
cat > packages/exporter/__main__.py <<'EOF'
"""Export worker — polls /api/v1/exports for queued jobs and processes them."""
from __future__ import annotations

import logging
import os
import sys
import time

import httpx

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass

from packages.exporter.pipeline import _check_tools, run_export_job

LOG_FMT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("exporter.worker")

API_URL = os.environ.get("SLM_FORGE_API_URL", "http://localhost:8000")
POLL_INTERVAL = float(os.environ.get("SLM_FORGE_POLL_INTERVAL", "2.0"))


def fetch_next_queued() -> dict | None:
    try:
        r = httpx.get(
            f"{API_URL}/api/v1/exports",
            params={"status": "queued", "limit": 1},
            timeout=5,
        )
        r.raise_for_status()
        rows = r.json()
        return rows[-1] if rows else None
    except Exception as e:  # noqa: BLE001
        log.warning("API poll failed: %s", e)
        return None


def main() -> int:
    log.info("Exporter worker starting (API=%s, poll=%.1fs)", API_URL, POLL_INTERVAL)

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
        log.error("API never came up.")
        return 1

    try:
        quantize_bin, convert_script = _check_tools()
        log.info("llama-quantize: %s", quantize_bin)
        log.info("convert script: %s", convert_script)
        log.info("torch: verified importable")
    except RuntimeError as e:
        log.error("Pre-flight failed: %s", e)
        return 1

    log.info("Ready. Polling for queued exports every %.1fs (Ctrl-C to stop).", POLL_INTERVAL)

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
            log.exception("Error: %s", e)
            if export:
                try:
                    httpx.patch(
                        f"{API_URL}/api/v1/exports/{export['id']}",
                        json={"status": "failed", "error_message": str(e)[:500]},
                        timeout=10,
                    )
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
EOF

echo "✓ packages/exporter/__main__.py — aligned with new _check_tools() signature"

# Verify everything is callable
echo ""
echo "→ Verifying toolchain..."
echo -n "  torch: "
uv run python -c "import torch; print(torch.__version__)"
echo -n "  convert_hf_to_gguf.py: "
ls scripts/llama_cpp/convert_hf_to_gguf.py && echo "present" || echo "MISSING"
echo -n "  llama-quantize: "
which llama-quantize 2>/dev/null || echo "/opt/homebrew/bin/llama-quantize"

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Exporter patched (final version)                                  ║
╚══════════════════════════════════════════════════════════════════════╝

Pipeline (correct this time):
  1. mlx_lm.fuse           → fused HF safetensors (MLX-native)
  2. convert_hf_to_gguf.py → F16 GGUF (via torch, ~60MB dep)
  3. llama-quantize         → Q4_K_M / Q8_0 (from Homebrew's llama.cpp)

No more "No module named torch" — torch is now installed.
No more "--type gguf" errors — we use convert_hf_to_gguf.py properly.

Now:
  make exporter

  Then queue an export from the UI:
  /runs → pick completed run → "Export to GGUF →"
MSG
