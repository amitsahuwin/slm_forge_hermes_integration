"""CUDA training script — PEFT LoRA/DoRA (+ bitsandbytes NF4) via TRL.

Invoked by :class:`CudaBackend` as a subprocess:

    python -m packages.trainer.cuda_train --config /path/to/config.json

Metric contract (consumed by ``CudaBackend.parse_line``): one JSON object
per line on stdout, e.g.

    {"event": "metric", "step": 10, "name": "train_loss", "value": 2.5}

Design rules:
- **Import-safe**: torch/transformers/peft/trl are imported inside
  ``main()`` only, so unit tests (and the Mac) can import this module.
- **No secrets in config**: HF auth comes from the ambient ``HF_TOKEN``
  env var or a cached ``huggingface-cli login`` — never the config file.

See ``docs/specs/PHASE_Q_SPEC.md`` §3.3.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_KEYS = (
    "model", "data", "dataset_format", "mask_prompt", "fine_tune_type",
    "lora_rank", "lora_alpha", "batch_size", "iters", "learning_rate",
    "max_seq_length", "grad_checkpoint", "seed", "quant", "adapter_path",
    "steps_per_report", "steps_per_eval",
)


def load_config(path: Path | str) -> dict[str, Any]:
    """Load and validate the training config written by CudaBackend."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config missing required keys: {', '.join(sorted(missing))}")
    return cfg


def emit(event: dict[str, Any]) -> None:
    """Print one JSONL event line, flushed (the worker reads us line-buffered)."""
    print(json.dumps(event), flush=True)


def emit_metric(step: int, name: str, value: float) -> None:
    emit({"event": "metric", "step": int(step), "name": name, "value": float(value)})


def _is_gated_auth_error(exc: Exception) -> bool:
    """True for a Hugging Face gated-repo / auth failure (401 / 403)."""
    msg = str(exc).lower()
    return "gated repo" in msg or "401 client error" in msg or "403 client error" in msg


def gated_repo_help(model: str) -> str:
    """Actionable guidance for a gated-repo download failure."""
    return (
        f"Hugging Face denied access to '{model}'. This is a gated repo. To use it:\n"
        f"  1. Accept the license once at https://huggingface.co/{model}\n"
        f"  2. Provide a token: set HF_TOKEN in .env (the trainer worker loads it "
        f"at startup) or run `huggingface-cli login`.\n"
        f"Or pick a non-gated model (e.g. Qwen/Qwen2.5-3B-Instruct)."
    )


def _load_dataset(data_dir: Path, fmt: str):  # pragma: no cover - exercised on GPU
    from datasets import load_dataset

    files = {"train": str(data_dir / "train.jsonl")}
    if (data_dir / "valid.jsonl").exists():
        files["validation"] = str(data_dir / "valid.jsonl")
    ds = load_dataset("json", data_files=files)
    if fmt == "text" and "text" not in ds["train"].column_names:
        raise ValueError("text dataset must have a 'text' column")
    return ds


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs GPU stack
    parser = argparse.ArgumentParser(description="SLM-Forge CUDA trainer")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    emit({"event": "info", "message": f"loading {cfg['model']} (quant={cfg['quant']})"})

    # Heavy imports deferred — keep module import-safe.
    import torch
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainerCallback,
        set_seed,
    )
    from trl import SFTConfig, SFTTrainer

    set_seed(int(cfg["seed"]))

    quant_cfg = None
    if cfg["quant"] == "nf4":
        quant_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    try:
        tokenizer = AutoTokenizer.from_pretrained(cfg["model"])
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            cfg["model"],
            quantization_config=quant_cfg,
            torch_dtype=torch.bfloat16 if quant_cfg is None else None,
            device_map="auto",
        )
    except Exception as exc:
        # Turn the raw HF OSError/401 into something the user can act on; the
        # worker copies our final stderr into the run's error_message.
        if _is_gated_auth_error(exc):
            help_text = gated_repo_help(cfg["model"])
            emit({"event": "error", "message": help_text})
            raise RuntimeError(help_text) from exc
        raise
    if quant_cfg is not None:
        model = prepare_model_for_kbit_training(model)

    peft_cfg = LoraConfig(
        r=int(cfg["lora_rank"]),
        lora_alpha=int(cfg["lora_alpha"]),
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules="all-linear",
        use_dora=cfg["fine_tune_type"] == "dora",
    )

    dataset = _load_dataset(Path(cfg["data"]), cfg["dataset_format"])
    has_eval = "validation" in dataset

    sft_cfg = SFTConfig(
        output_dir=str(Path(cfg["adapter_path"]).parent / "hf_out"),
        max_steps=int(cfg["iters"]),
        per_device_train_batch_size=int(cfg["batch_size"]),
        learning_rate=float(cfg["learning_rate"]),
        max_length=int(cfg["max_seq_length"]),
        gradient_checkpointing=bool(cfg["grad_checkpoint"]),
        logging_steps=int(cfg["steps_per_report"]),
        eval_strategy="steps" if has_eval else "no",
        eval_steps=int(cfg["steps_per_eval"]) if has_eval else None,
        save_strategy="no",
        seed=int(cfg["seed"]),
        report_to=[],
        bf16=True,
        # chat datasets: loss on assistant tokens only (mask_prompt parity)
        assistant_only_loss=bool(cfg["mask_prompt"]) and cfg["dataset_format"] == "chat",
        dataset_text_field="text" if cfg["dataset_format"] == "text" else None,
    )

    class MetricEmitter(TrainerCallback):
        def on_log(self, args_, state, control, logs=None, **kw):
            if not logs:
                return
            step = int(state.global_step)
            if "loss" in logs:
                emit_metric(step, "train_loss", logs["loss"])
            if "learning_rate" in logs:
                emit_metric(step, "learning_rate", logs["learning_rate"])
            if "train_steps_per_second" in logs:
                emit_metric(step, "iters_per_sec", logs["train_steps_per_second"])
            if "train_tokens_per_second" in logs:
                emit_metric(step, "tokens_per_sec", logs["train_tokens_per_second"])

        def on_evaluate(self, args_, state, control, metrics=None, **kw):
            if metrics and "eval_loss" in metrics:
                emit_metric(int(state.global_step), "val_loss", metrics["eval_loss"])

    trainer = SFTTrainer(
        model=model,
        args=sft_cfg,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"] if has_eval else None,
        peft_config=peft_cfg,
        processing_class=tokenizer,
        callbacks=[MetricEmitter()],
    )

    trainer.train()

    adapter_path = Path(cfg["adapter_path"])
    adapter_path.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    emit({"event": "info", "message": f"adapter saved to {adapter_path}"})
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
