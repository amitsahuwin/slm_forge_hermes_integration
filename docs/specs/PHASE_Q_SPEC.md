# Phase Q Spec — CUDA training backend (PEFT + TRL + bitsandbytes)

> **Status:** approved for implementation · **Date:** 2026-06-12
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` §4.2, §4.5, §5
> **Depends on:** Phase O (`TrainerBackend`), Phase P (catalog `cuda` variants)

---

## 1. Problem

The backend seam exists (Phase O) and the catalog already names CUDA
checkpoints (Phase P), but `_REGISTRY` contains only `mlx`. The upcoming
A100 boxes need a worker that trains with the canonical CUDA stack —
transformers + PEFT (LoRA/DoRA) + TRL `SFTTrainer` + bitsandbytes NF4
QLoRA — and the exporter must merge PEFT adapters, which `mlx_lm fuse`
cannot do.

## 2. Goals / non-goals

**Goals**

- G1. `CudaBackend` registered as `"cuda"`, implementing the full
  `TrainerBackend` contract with **JSONL stdout metrics** (no regex).
- G2. `packages/trainer/cuda_train.py` — the training script the backend
  invokes as a subprocess. Heavy imports (torch/trl/peft) happen inside
  `main()` only, so the module is importable (and unit-testable) anywhere.
- G3. Shared dataset helpers (`_detect_dataset_format`, `_count_jsonl`)
  move to `packages/trainer/backends/dataset_utils.py`; `mlx.py` re-uses
  them (no behavior change — Phase O parity tests must stay green).
- G4. Exporter: stage 1 branches on **adapter format detection** —
  `adapter_model.safetensors` ⇒ PEFT ⇒ new `packages/exporter/peft_merge.py`
  (`merge_and_unload` → fp16 `save_pretrained`); `adapters.safetensors` ⇒
  MLX ⇒ existing `mlx_lm fuse`. Stages 2–3 (GGUF convert + quantize) are
  untouched.
- G5. Packaging: `[project.optional-dependencies] trainer-cuda`,
  `Dockerfile.trainer-cuda` (CUDA 12 runtime base), `make trainer-cuda`,
  `.env.example` notes (`SLM_FORGE_TRAINER_BACKEND=cuda`, `HF_TOKEN`).
- G6. Secrets hygiene: HF auth is **only** via the `HF_TOKEN` env var /
  `huggingface-cli login`; never a config file or repo content.

**Non-goals:** multi-GPU/FSDP, DPO/RLHF, CUDA canary eval (follow-up),
backend-filtered queue claiming (Phase R), UI (Phase S).

## 3. Interfaces

### 3.1 `packages/trainer/backends/dataset_utils.py` (new, moved code)

`detect_dataset_format(dataset_dir) -> "chat" | "text"` and
`count_jsonl(path) -> int` — verbatim logic from `mlx.py`, which now
imports them (module-level aliases keep its internal call sites identical).

### 3.2 `CudaBackend` (`packages/trainer/backends/cuda.py`)

- `name = "cuda"`; registered in `_REGISTRY`.
- `write_config(run, dataset_dir, adapter_dir) -> Path` — writes
  `config.json` (sibling of the adapter dir, mirroring mlx's
  `config.yaml`):

```json
{
  "model": "<run.base_model>",
  "data": "<dataset_dir>",
  "dataset_format": "chat|text",
  "mask_prompt": true,
  "fine_tune_type": "lora|dora|full",
  "lora_rank": 16, "lora_alpha": 32,
  "batch_size": 4, "iters": 200, "learning_rate": 1e-4,
  "max_seq_length": 2048, "grad_checkpoint": false, "seed": 0,
  "quant": "nf4",
  "adapter_path": "<adapter_dir>",
  "steps_per_report": 10,
  "steps_per_eval": "max(20, iters // 10)"
}
```

  `quant` comes from `SLM_FORGE_CUDA_QUANT` (default `"nf4"`; `"none"`
  disables 4-bit loading for big-VRAM full-precision LoRA).
- `build_command(config_path)` — probes the toolchain once via
  `subprocess.run([py, "-c", "import torch, transformers, peft, trl"])`;
  on success returns
  `[py, "-m", "packages.trainer.cuda_train", "--config", <path>]`,
  else `None`.
- `parse_line(line)` — accepts only JSON objects with
  `{"event": "metric", "step": int, "name": str, "value": num}` →
  `[TrainEvent]`; everything else (tqdm bars, warnings, non-metric
  events) → `[]`. Malformed JSON must never raise.
- `missing_toolchain_message()` — names the extras group and the env var.

### 3.3 `packages/trainer/cuda_train.py`

- Import-safe module; argparse `--config`. Pure helpers exposed for tests:
  `load_config(path) -> dict` (validates required keys) and
  `emit_metric(step, name, value)` (prints the JSONL contract line,
  flushed).
- `main()` (torch imported here): seed → tokenizer →
  `BitsAndBytesConfig(load_in_4bit, nf4, double_quant, bf16 compute)` when
  `quant == "nf4"` → `AutoModelForCausalLM` (`device_map="auto"`) →
  `prepare_model_for_kbit_training` → `LoraConfig(r, alpha,
  target_modules="all-linear", use_dora=fine_tune_type=="dora")` → TRL
  `SFTTrainer` with `max_steps=iters`, batch size, lr, seq length,
  gradient checkpointing, eval on `valid.jsonl` every `steps_per_eval`,
  `assistant_only_loss` for chat datasets (mask_prompt semantics).
- A `TrainerCallback` emits: `on_log` → `train_loss`, `learning_rate`
  (+ `iters_per_sec` when present in logs); `on_evaluate` → `val_loss`.
- Saves the PEFT adapter to `adapter_path` (produces
  `adapter_model.safetensors` + `adapter_config.json`) — the same
  directory contract the API already stores.
- HF auth: relies on ambient `HF_TOKEN` / cached login. The script never
  reads tokens from its config file.

### 3.4 Exporter (`packages/exporter/`)

- `detect_adapter_format(adapter_dir) -> "peft" | "mlx"` in `pipeline.py`:
  `adapter_model.safetensors` present → `peft`; else `mlx` (MLX writes
  `adapters.safetensors`; both formats include an `adapter_config.json`,
  so file *names* are the discriminator, verified against real run dirs).
- `peft_merge.py` (new, import-safe like `cuda_train.py`): CLI
  `--base <hf_id> --adapter <dir> --out <dir>`; loads the base model fp16
  on CPU/GPU, `PeftModel.from_pretrained(...).merge_and_unload()`,
  `save_pretrained(out)` + tokenizer — producing the same "fused fp16 HF
  dir" contract stage 2 already consumes.
- Stage 1 of `run_export_job` branches on the detection; progress text and
  failure handling mirror the mlx path.

### 3.5 Packaging / ops

- `pyproject.toml`:
  `trainer-cuda = ["torch>=2.4", "transformers>=4.46", "peft>=0.13",
  "trl>=0.12", "bitsandbytes>=0.44", "datasets>=3.1", "accelerate>=1.0"]`
- `Dockerfile.trainer-cuda`: `nvidia/cuda:12.4.1-runtime-ubuntu22.04` +
  Python 3.12 + `pip install -e .[trainer-cuda]`; entrypoint runs
  `python -m packages.trainer` with `SLM_FORGE_TRAINER_BACKEND=cuda`;
  `HF_TOKEN` and `SLM_FORGE_API_URL`/`SLM_FORGE_SERVICE_TOKEN` passed at
  runtime, never baked in.
- `Makefile`: `trainer-cuda` target (bare-metal Linux convenience:
  `SLM_FORGE_TRAINER_BACKEND=cuda uv run python -m packages.trainer`).
- `.env.example`: `HF_TOKEN` placeholder + cuda backend note.

## 4. Acceptance criteria

- A1. Registry: `get_backend("cuda")` → `CudaBackend`; env var selection
  works; error message for junk names now lists both `cuda` and `mlx`.
- A2. Config: golden `config.json` for a fixed run dict (chat and text
  datasets — `mask_prompt`/`dataset_format` flip correctly); quant
  override via `SLM_FORGE_CUDA_QUANT`.
- A3. Parsing: metric JSONL → correct `TrainEvent`s; noise (tqdm lines,
  warnings, `{"event":"info"}`, malformed JSON) → `[]` without raising.
- A4. Command: toolchain probe success → exact argv; probe failure →
  `None` and a message naming `trainer-cuda` extras.
- A5. `cuda_train` helpers: `load_config` validates required keys (raises
  `ValueError` listing missing ones); `emit_metric` prints a line
  `parse_line` round-trips into the equivalent `TrainEvent`.
- A6. Exporter: `detect_adapter_format` correct on PEFT-shaped, MLX-shaped
  (real layout incl. checkpoint files), and ambiguous/empty dirs (default
  `mlx`); `peft_merge.py` is importable without torch and rejects a
  missing adapter dir before any heavy import.
- A7. Phase O mlx parity tests still green after the dataset-utils move;
  full suite green; ruff clean on touched files.

## 5. Test plan

```
tests/trainer/test_cuda_backend.py     # A1–A4
tests/trainer/test_cuda_train_script.py # A5
tests/exporter/test_adapter_format.py  # A6
```

Hermetic: no torch, no network, no GPU. The CUDA *execution* path is
validated on real hardware per §6.

## 6. Hardware verification (manual, post-merge)

On any CUDA box (rented GPU is fine, pre-A100):
`pip install -e .[trainer-cuda]` → `SLM_FORGE_TRAINER_BACKEND=cuda
python -m packages.trainer` against the Mac-hosted API → run
`qwen2.5-3b-instruct` (CUDA variant) for 30 iters → verify live loss
charts, adapter upload location, and a full export (PEFT merge → GGUF).
Note: dataset/artifact paths assume a shared filesystem until Phase R
adds transfer endpoints — for the trial, run API + CUDA worker on the
same box or NFS-mount `data/` and `runs/`.

## 7. Rollout

Additive only. Mac deployments see zero change (`mlx` remains default).
The exporter's PEFT branch only triggers for adapters a CUDA worker
produced. `trainer-cuda` extras are never installed on macOS
(bitsandbytes has no Metal support — documented in the extras comment).
