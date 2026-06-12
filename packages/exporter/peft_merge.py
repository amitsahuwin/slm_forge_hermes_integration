"""Merge a PEFT LoRA adapter into its base model → fp16 HF safetensors.

The CUDA-backend equivalent of ``mlx_lm fuse --dequantize`` (Phase Q):
loads the base model in fp16/bf16, applies the adapter, calls
``merge_and_unload()``, and saves a plain HF model directory that the
existing GGUF conversion stage consumes unchanged.

    python -m packages.exporter.peft_merge \
        --base Qwen/Qwen2.5-3B-Instruct \
        --adapter runs/12/adapter \
        --out exports/3/fused

Import-safe: torch/peft are imported only after argument validation, so
the exporter worker (and the test suite) can import this module on
machines without the CUDA stack.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge PEFT adapter into base model")
    parser.add_argument("--base", required=True, help="HF id of the base model")
    parser.add_argument("--adapter", required=True, help="PEFT adapter directory")
    parser.add_argument("--out", required=True, help="Output dir for merged fp16 model")
    args = parser.parse_args(argv)

    adapter_dir = Path(args.adapter)
    if not (adapter_dir / "adapter_model.safetensors").exists():
        print(f"ERROR: no PEFT adapter at {adapter_dir}", file=sys.stderr)
        raise SystemExit(2)

    # Heavy imports deferred until inputs are sane.
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"Loading base model {args.base} (fp16)…", flush=True)
    base = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.float16, device_map="auto"
    )
    print(f"Applying adapter {adapter_dir}…", flush=True)
    merged = PeftModel.from_pretrained(base, str(adapter_dir)).merge_and_unload()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Saving merged model → {out}", flush=True)
    merged.save_pretrained(str(out), safe_serialization=True)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(str(out))
    print("Done.", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
