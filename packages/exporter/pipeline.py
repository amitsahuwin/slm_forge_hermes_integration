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


def detect_adapter_format(adapter_dir: Path) -> str:
    """Return ``"peft"`` or ``"mlx"`` based on the adapter's file layout.

    Both formats write an ``adapter_config.json``, so file *names* are the
    discriminator (verified against real run dirs):
      - PEFT ``save_pretrained``: ``adapter_model.safetensors``
      - MLX-LM:                   ``adapters.safetensors``
    Defaults to ``"mlx"`` (the historical format) when ambiguous.
    """
    if (adapter_dir / "adapter_model.safetensors").exists():
        return "peft"
    return "mlx"


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

    # ── Stage 1: merge adapter into base (backend-dependent) ─────────
    # Phase Q: the adapter's on-disk format tells us which trainer made it.
    adapter_format = detect_adapter_format(adapter_dir)

    if adapter_format == "peft":
        log.info("Stage 1/3: PEFT merge_and_unload (CUDA-trained adapter)")
        _patch_export(api_url, xid, status="fusing",
                      progress_text="Merging PEFT adapter into base model…")
        fuse_cmd = [
            str(VENV_PYTHON), "-m", "packages.exporter.peft_merge",
            "--base", base_model,
            "--adapter", str(adapter_dir),
            "--out", str(fused_dir),
        ]
        rc = _run_subprocess(fuse_cmd, log_path, env=env, cwd=str(PROJECT_ROOT))
        if rc != 0:
            msg = f"peft_merge exited {rc}. See {log_path}"
            log.error(msg)
            _patch_export(api_url, xid, status="failed", error_message=msg)
            return
    else:
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
