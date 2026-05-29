"""Runs one export job: LoRA adapter → fused HF → GGUF F16 → quantized variants."""
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

# DB field name → ExportPatch field name for storing each variant's path & size
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


def _patch_export(api_url: str, xid: int, **fields: Any) -> None:
    try:
        httpx.patch(f"{api_url}/api/v1/exports/{xid}", json=fields, timeout=10).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PATCH /exports/%s failed: %s", xid, e)


def _find_llama_quantize() -> str | None:
    """Locate the llama-quantize binary (Homebrew or PATH)."""
    candidates = [
        "llama-quantize",
        "/opt/homebrew/bin/llama-quantize",
        "/usr/local/bin/llama-quantize",
    ]
    for c in candidates:
        found = shutil.which(c) if "/" not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


def _find_convert_script() -> str | None:
    """Locate convert_hf_to_gguf.py.

    Priority:
      1. scripts/llama_cpp/convert_hf_to_gguf.py  (downloaded by patch_llamacpp_convert.sh)
      2. Homebrew share/libexec paths
      3. brew --prefix probe
    """
    import glob

    # 1. Project-local copy (most reliable)
    local = PROJECT_ROOT / "scripts" / "llama_cpp" / "convert_hf_to_gguf.py"
    if local.exists():
        return str(local)

    # 2. Homebrew standard paths
    candidates = [
        "/opt/homebrew/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/libexec/llama.cpp/convert_hf_to_gguf.py",
        "/usr/local/share/llama.cpp/convert_hf_to_gguf.py",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    # 3. glob for versioned cellar paths
    for pattern in [
        "/opt/homebrew/Cellar/llama.cpp/*/share/llama.cpp/convert_hf_to_gguf.py",
        "/opt/homebrew/Cellar/llama.cpp/*/libexec/convert_hf_to_gguf.py",
    ]:
        for path in glob.glob(pattern):
            if os.path.exists(path):
                return path

    # 4. brew --prefix probe
    try:
        r = subprocess.run(
            ["brew", "--prefix", "llama.cpp"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            prefix = r.stdout.strip()
            for sub in ("share/llama.cpp", "libexec"):
                p = Path(prefix) / sub / "convert_hf_to_gguf.py"
                if p.exists():
                    return str(p)
    except Exception:  # noqa: BLE001
        pass

    return None


def _check_tools() -> tuple[str, str]:
    """Verify llama.cpp is installed. Returns (quantize_path, convert_script_path)."""
    q = _find_llama_quantize()
    if not q:
        raise RuntimeError(
            "llama-quantize not found. Install: brew install llama.cpp"
        )
    c = _find_convert_script()
    if not c:
        raise RuntimeError(
            "convert_hf_to_gguf.py not found. "
            "Verify with: brew list llama.cpp | grep convert_hf_to_gguf.py"
        )
    return q, c


def _run_subprocess(cmd: list[str], log_path: Path, *, env: dict | None = None) -> int:
    """Run a subprocess, streaming output to both stdout and the log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(cmd)}\n\n")
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
    """Run one export end-to-end."""
    xid = export_row["id"]
    run_id = export_row["run_id"]
    base_model = export_row["base_model"]
    quant_levels = [q.strip() for q in export_row["quant_levels"].split(",") if q.strip()]

    log.info("─── Export #%s for run #%s (quants=%s) ───", xid, run_id, quant_levels)

    # ── Sanity: tools ────────────────────────────────────────────
    try:
        quantize_bin, convert_script = _check_tools()
    except RuntimeError as e:
        log.error(str(e))
        _patch_export(api_url, xid, status="failed", error_message=str(e))
        return

    # ── Sanity: adapter exists ───────────────────────────────────
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

    # ── Stage 1: mlx_lm.fuse → safetensors ───────────────────────
    log.info("Stage 1/3: mlx_lm.fuse (LoRA adapter + base → merged safetensors)")
    _patch_export(api_url, xid, status="fusing", progress_text="Fusing LoRA into base model…")

    py = sys.executable
    fuse_cmd = [
        py, "-m", "mlx_lm", "fuse",
        "--model", base_model,
        "--adapter-path", str(adapter_dir),
        "--save-path", str(fused_dir),
    ]
    # Older mlx-lm versions used 'mlx_lm.fuse' as a direct module
    probe = subprocess.run(
        [py, "-m", "mlx_lm", "fuse", "--help"], capture_output=True, text=True, timeout=15
    )
    if probe.returncode != 0:
        fuse_cmd = [
            py, "-m", "mlx_lm.fuse",
            "--model", base_model,
            "--adapter-path", str(adapter_dir),
            "--save-path", str(fused_dir),
        ]

    env = os.environ.copy()
    scripts = sysconfig.get_path("scripts")
    if scripts:
        env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"

    rc = _run_subprocess(fuse_cmd, log_path, env=env)
    if rc != 0:
        msg = f"mlx_lm.fuse exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(api_url, xid, fused_path=str(fused_dir))

    # ── Stage 2: convert_hf_to_gguf.py → F16 GGUF ────────────────
    log.info("Stage 2/3: convert_hf_to_gguf.py (HF safetensors → F16 GGUF)")
    _patch_export(api_url, xid, status="converting", progress_text="Converting to F16 GGUF…")

    f16_path = gguf_dir / QUANT_FILENAME["F16"]
    # Ensure the local gguf helpers are importable alongside convert_hf_to_gguf.py
    convert_env = dict(env)
    convert_script_dir = str(Path(convert_script).parent)
    pythonpath = convert_env.get("PYTHONPATH", "")
    convert_env["PYTHONPATH"] = (
        f"{convert_script_dir}:{pythonpath}" if pythonpath else convert_script_dir
    )
    convert_cmd = [
        py, convert_script,
        str(fused_dir),
        "--outtype", "f16",
        "--outfile", str(f16_path),
    ]
    rc = _run_subprocess(convert_cmd, log_path, env=convert_env)
    if rc != 0:
        msg = f"convert_hf_to_gguf.py exited with code {rc}. See {log_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    if not f16_path.exists():
        msg = f"Conversion succeeded but F16 GGUF not at expected path: {f16_path}"
        log.error(msg)
        _patch_export(api_url, xid, status="failed", error_message=msg)
        return

    _patch_export(
        api_url, xid,
        gguf_f16_path=str(f16_path),
        gguf_f16_bytes=f16_path.stat().st_size,
    )

    # ── Stage 3: llama-quantize → Q4_K_M / Q5_K_M / Q8_0 ─────────
    log.info("Stage 3/3: llama-quantize (F16 → user-selected quants)")
    _patch_export(api_url, xid, status="quantizing", progress_text="Quantizing variants…")

    for quant in quant_levels:
        if quant == "F16":
            continue  # already produced

        target = gguf_dir / QUANT_FILENAME[quant]
        log.info("  quantizing → %s", target.name)
        _patch_export(api_url, xid, progress_text=f"Quantizing {quant}…")

        rc = _run_subprocess(
            [quantize_bin, str(f16_path), str(target), quant],
            log_path,
            env=env,
        )
        if rc != 0:
            msg = f"llama-quantize {quant} exited with code {rc}. See {log_path}"
            log.error(msg)
            _patch_export(api_url, xid, status="failed", error_message=msg)
            return

        if target.exists():
            _patch_export(
                api_url, xid,
                **{DB_FIELD_PATH[quant]: str(target), DB_FIELD_BYTES[quant]: target.stat().st_size},
            )

    # ── Done ─────────────────────────────────────────────────────
    log.info("Export #%s completed.", xid)
    _patch_export(
        api_url, xid,
        status="completed",
        progress_text="Done. Send the .gguf to your iPhone (see docs/IPHONE_DEPLOY.md).",
    )
