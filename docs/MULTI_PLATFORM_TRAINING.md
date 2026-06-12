# Multi-Platform Fine-Tuning Plan — Gemma 3 / Gemma 4 / Mistral 7B on Apple Silicon today, A100 tomorrow

> **Status:** proposed (2026-06-12) · **Owner:** Amit · **Targets:** M3 Max 36GB (now), A100 Linux boxes (next)
>
> This document answers two questions and turns the answers into a phased
> engineering plan: **(1)** can the M3 Max 36GB MacBook fine-tune Gemma 3,
> Gemma 4 (E2B/E4B), or Mistral 7B — and with which stack; **(2)** how do we
> evolve SLM-Forge from a single-backend (MLX-only) trainer into a
> backend-pluggable system that runs the same training job on Apple Silicon
> via MLX or on CUDA/A100 via PEFT + transformers, with no changes to the
> API, UI, or export pipeline contract.

---

## 1. The verdict: your assumption is (happily) outdated

`mlx-lm` **does** support LoRA / QLoRA / DoRA fine-tuning for the Gemma
family and Mistral 7B. The supported-architecture list for `mlx_lm.lora`
includes Llama, **Mistral**, Qwen, Phi, **Gemma** (1/2/3), Mixtral, and more,
and community guides routinely fine-tune `google/gemma-3-*-it` and
`mlx-community/gemma-3-4b-it-4bit` on Apple Silicon.

The nuance — and probably where the "MLX doesn't support Gemma" impression
came from — is **version skew**:

- Our repo pins `mlx == 0.31.2` / `mlx-lm == 0.31.3` (see `uv.lock`), and our
  own model catalog (`apps/api/routers/models.py`) marks
  `mlx-community/gemma-3n-E2B-it-bf16` as **broken on 0.31.3** (`KeyError`
  in `sanitize()`). That is a stale-version problem, not a capability gap.
- **Gemma 4** (E2B / E4B / 26B-A4B / 31B, the MatFormer successors to
  Gemma 3n) shipped with `model type gemma4 not supported` errors on older
  MLX builds; current `mlx-lm` releases load and LoRA-train them, and
  `mlx-community` publishes 4-bit MLX conversions of all Gemma 4 sizes.

So the first concrete action of this plan is simply: **upgrade `mlx-lm`**,
then widen the catalog. PEFT/transformers is *not* required to train Gemma
on the Mac — it is the right stack for the A100s (section 4).

---

## 2. What fits in 36 GB (M3 Max, unified memory)

Unified memory means weights + optimizer state + activations + macOS all
share the same 36 GB. Budget ~8 GB for the OS / Ollama / browser and treat
**~28 GB as the training ceiling**. Estimates below assume LoRA rank ≤ 16,
`batch_size 1–2`, `max_seq_length 1024–2048`, `grad_checkpoint: true` for
anything ≥ 7B.

| Model | Raw params | Method (recommended) | Est. peak memory | Verdict on 36 GB |
|---|---|---|---|---|
| Gemma 3 4B-it | 4B | QLoRA on 4-bit (`mlx-community/gemma-3-4b-it-4bit`) | ~5–7 GB | ✅ Comfortable, good batch sizes |
| Gemma 3 12B-it | 12B | QLoRA on 4-bit | ~12–16 GB | ✅ Works; keep seq ≤ 2048 |
| Gemma 3 27B-it | 27B | QLoRA on 4-bit, batch 1, grad-ckpt | ~22–27 GB | ⚠️ Possible but slow and tight — not a daily driver |
| Gemma 4 E2B (≈2B effective) | ~5B raw | LoRA bf16 or QLoRA | ~6–10 GB | ✅ Comfortable |
| Gemma 4 E4B (≈4B effective) | ~8B raw | QLoRA on 4-bit | ~9–12 GB | ✅ Recommended sweet spot |
| Gemma 4 E4B | ~8B raw | LoRA bf16 | ~17–20 GB | ✅ Fits (community guides cite ~17 GB) |
| Mistral 7B v0.3 | 7B | QLoRA on 4-bit (`mlx-community/Mistral-7B-Instruct-v0.3-4bit`) | ~7–9 GB | ✅ Comfortable |
| Mistral 7B v0.3 | 7B | LoRA bf16 | ~18–22 GB | ✅ Fits with grad-ckpt |

Numbers are planning estimates, not lab measurements — Phase P includes a
smoke-test matrix to replace them with observed peaks from `mactop` /
`sudo powermetrics`.

Practical knobs that matter most on unified memory, in order: sequence
length (activation memory scales with it), batch size, `grad_checkpoint`,
quantized base (QLoRA) vs bf16, and LoRA `num_layers` (we default to 16).

### MLX vs PEFT/transformers on the Mac

Both *can* run on Apple Silicon, but MLX is roughly **3× faster than
PyTorch-MPS** on M-series for this workload (the reason we picked it —
see `docs/PLAN.md`, Architecture table). PEFT-on-MPS also has recurring
dtype/operator gaps (bf16 quirks, no bitsandbytes on macOS, so no true
QLoRA). Decision: **MLX stays the only macOS backend; PEFT/transformers
becomes the CUDA backend.** No effort is spent making PEFT run on Metal.

---

## 3. Where the codebase is today (single-backend reality)

The trainer worker (`packages/trainer/`) is a polling host process that
shells out to `mlx_lm lora --config config.yaml` and regex-parses stdout
for metrics. It is clean but MLX-shaped end to end:

| Constraint | Where | Why it blocks a second backend |
|---|---|---|
| Monolithic `run_training_job()` | `packages/trainer/runner.py` | No `TrainerBackend` seam; MLX command building, YAML config, regex metric parsing all inline |
| MLX-only config format | `_write_yaml_config()` in `runner.py` | `mask_prompt`, `val_batches`, `fine_tune_type` are mlx-lm semantics |
| stdout regex metrics | `_ITER_TRAIN` / `_ITER_VAL` in `runner.py` | HF `Trainer`/TRL emit a completely different format |
| MLX-only model catalog | `apps/api/routers/models.py` | Catalog entries are `mlx-community/*` IDs; CUDA needs full-precision HF IDs |
| No `trainer_backend` on `Run` | `apps/api/models/run.py` | API/UI can't express "run this on the A100 box" |
| Exporter hard-depends on `mlx_lm fuse` | `packages/exporter/pipeline.py` | CUDA adapters need `peft merge_and_unload` instead; GGUF stages after that are already backend-neutral |

Two existing strengths to preserve: the **subprocess invocation pattern**
(the worker never imports mlx/torch — perfect for divergent backend
dependency sets) and the **API-polling worker model** (a CUDA worker on a
remote A100 box can poll the same API with the existing service-token
auth — no new control plane needed).

---

## 4. Target architecture: pluggable trainer backends

One contract, N implementations. The worker grows a small backend protocol;
everything above it (API, DB, SSE, UI charts) keeps speaking the same
normalized language it already speaks.

```
                         ┌────────────────────────────┐
   POST /runs            │  Run (DB)                  │
   { base_model,         │  + trainer_backend: enum   │
     trainer_backend }   │    "mlx" | "cuda"          │
                         └─────────────┬──────────────┘
                                       │ poll (status=queued,
                                       │       backend=mine)
              ┌────────────────────────┼───────────────────────┐
              ▼                        ▼                       ▼
   trainer worker (Mac)      trainer worker (A100 #1)   trainer worker (A100 #2)
   SLM_FORGE_TRAINER_        SLM_FORGE_TRAINER_         SLM_FORGE_TRAINER_
   BACKEND=mlx               BACKEND=cuda               BACKEND=cuda
              │                        │                       │
   MlxBackend │             CudaBackend│ (PEFT+TRL+bnb)        │
   mlx_lm lora --config     python -m slm_forge_cuda_train     │
   (YAML, stdout regex)     (JSON config, JSONL metrics)       │
              └────────────────────────┴───────────────────────┘
                              normalized metric POSTs
                          /api/v1/runs/{id}/metrics (unchanged)
```

### 4.1 The `TrainerBackend` protocol

```python
class TrainerBackend(Protocol):
    name: str                                    # "mlx" | "cuda"

    def supports(self, run: RunSpec) -> Capability   # model/method/quant check
    def write_config(self, run: RunSpec, workdir: Path) -> Path
    def build_command(self, config_path: Path) -> list[str]
    def parse_event(self, line: str) -> TrainEvent | None   # → normalized
    def adapter_artifacts(self, workdir: Path) -> AdapterBundle
```

`TrainEvent` is the normalized union we already implicitly have
(`train_loss`, `val_loss`, `learning_rate`, `iters_per_sec`,
`tokens_per_sec`, `canary_loss`) — the MLX regexes and the CUDA JSONL
reader both map into it, so `/metrics` POSTs, SSE events, and the UI loss
charts need **zero changes**.

### 4.2 The CUDA backend (PEFT + transformers + TRL)

A new host/Docker entry point, `packages/trainer/cuda_train.py`, executed
as a subprocess by the same worker loop:

- **Stack:** `transformers` + `peft` (LoRA/DoRA) + `trl` (`SFTTrainer`) +
  `bitsandbytes` (4-bit NF4 QLoRA) + optional `unsloth` behind a flag for
  2× speed on single-GPU.
- **Config:** JSON file mirroring the `Run` hyperparams (the YAML writer
  becomes one of two config writers behind the protocol).
- **Metrics:** the script attaches a `TrainerCallback` that prints one JSON
  object per logging step to stdout (`{"step": n, "loss": …, "lr": …}`),
  which `parse_event` consumes. Structured JSONL beats regex — and we keep
  the regex path for MLX untouched.
- **Masking:** TRL's `assistant_only_loss` / completion-masking replaces
  MLX's `mask_prompt`; the existing `_detect_dataset_format()` chat-vs-text
  detection is reused as-is since the dataset JSONL formats are identical.
- **Artifacts:** PEFT adapter (`adapter_model.safetensors` +
  `adapter_config.json`) under the same `/app/runs/{run_id}/adapter/`
  layout the API already records.

### 4.3 Model catalog v2

Catalog entries become backend-aware, with one logical model mapping to
per-backend physical IDs:

```python
{
  "key": "gemma-4-e4b-it",
  "label": "Gemma 4 E4B (instruct)",
  "family": "gemma",
  "backends": {
    "mlx":  {"model_id": "mlx-community/gemma-4-E4B-it-4bit", "min_mem_gb": 12},
    "cuda": {"model_id": "google/gemma-4-E4B-it", "quant": "nf4", "min_vram_gb": 17},
  },
  "recommended_method": "lora",
}
```

Launch catalog: Gemma 3 4B / 12B, Gemma 4 E2B / E4B, Mistral 7B v0.3,
plus the existing Qwen 2.5 3B/7B and Llama 3.2 3B rows migrated into the
new shape. Validation finally enforces the catalog (today `base_model`
accepts any string silently).

### 4.4 Remote A100 workers

The polling model already does the heavy lifting: a CUDA worker on a Linux
box sets `SLM_FORGE_API_URL` to the API host and authenticates with the
existing `SLM_FORGE_SERVICE_TOKEN`. Three gaps to close:

1. **Backend-aware claiming** — `GET /runs?status=queued` grows a
   `backend=` filter, and claiming becomes an atomic lease
   (`PATCH … {status: running, claimed_by: worker_id}` with a
   compare-and-swap on status) so a Mac worker and an A100 worker never
   grab the same run.
2. **Dataset/artifact transfer** — remote workers can't see the Mac's
   filesystem. Add `GET /datasets/{id}/archive` (download) and
   `POST /runs/{id}/artifacts` (adapter upload) so all file movement flows
   through the API. Local MLX workers keep using the shared path as a
   fast path.
3. **Packaging** — a `Dockerfile.trainer-cuda` (CUDA 12.x base, pinned
   torch/trl/peft/bitsandbytes) plus a `make trainer-cuda` target. The MLX
   trainer remains host-only (Metal is unreachable from containers — the
   constraint documented in `docs/PLAN.md` stands, but it's now an
   MLX-backend constraint, not a system constraint).

### 4.5 Exporter convergence

Both backends end at the same place — merged fp16 HF safetensors — after
which the existing GGUF conversion + quantization stages run unchanged:

- MLX adapters: `mlx_lm fuse --dequantize` (today's path, unchanged).
- PEFT adapters: `PeftModel.from_pretrained(...).merge_and_unload()` →
  `save_pretrained()` (new `_merge_peft()` stage selected by the run's
  `trainer_backend`).

---

## 5. Phased plan

`docs/PLAN.md` ends at Phase N, so this work picks up at **O**.

| Phase | Title | Scope | Risk |
|---|---|---|---|
| **O** | Backend abstraction (no behavior change) | Extract `TrainerBackend` protocol from `runner.py`; `MlxBackend` as sole implementation; add `Run.trainer_backend` (default `"mlx"`, additive column); add `SLM_FORGE_TRAINER_BACKEND` env (default `mlx`); normalized `TrainEvent`. Existing MLX runs must be bit-identical in behavior. | Low |
| **P** | mlx-lm upgrade + catalog v2 | Bump `mlx`/`mlx-lm` to current; backend-aware catalog with Gemma 3 4B/12B, Gemma 4 E2B/E4B, Mistral 7B; enforce catalog validation; **smoke-test matrix on the M3 Max** recording real peak memory + tokens/sec into section 2's table. | Medium (mlx-lm CLI flags can drift between versions — pin and test) |
| **Q** | CUDA backend | `cuda_train.py` (PEFT+TRL+bnb, JSONL metrics), `CudaBackend` implementation, `Dockerfile.trainer-cuda`, `make trainer-cuda`, PEFT merge stage in exporter. Verifiable on any rented CUDA GPU before the A100s arrive. | Medium |
| **R** | Remote worker hardening | Atomic run claiming with `claimed_by` + lease timeout, `backend=` queue filter, dataset archive download + adapter upload endpoints, worker heartbeats surfaced in the dashboard. | Medium |
| **S** | UX polish | Backend picker on the New Run form (filtered by catalog capability), per-backend defaults (batch size, grad-ckpt), memory-fit hints from catalog `min_mem_gb` vs detected hardware. | Low |

Phases O→P are pure-local and unblock Gemma/Mistral on the MacBook
immediately. Q can be developed and tested against a cheap cloud GPU;
R only matters once the A100 boxes are real.

### Quick wins (this week, before Phase O lands)

You don't need any refactor to try Gemma on the Mac today:

```bash
uv add -U mlx mlx-lm                       # 1. upgrade past 0.31.x
python -m mlx_lm lora \                    # 2. manual smoke test
  --model mlx-community/gemma-4-E4B-it-4bit \
  --train --data <dataset dir> \
  --batch-size 1 --num-layers 16 --grad-checkpoint \
  --iters 100
```

If that run completes, add the model ID to today's `CATALOG` list as-is and
the whole existing pipeline (UI → trainer → exporter → GGUF → Ollama)
should work — Gemma 3/4 GGUF conversion is supported by current llama.cpp.
Re-test the `gemma-3n-E2B` catalog entry after the upgrade; the
`sanitize()` KeyError was a 0.31.x-era bug.

---

## 6. Risks & open questions

- **mlx-lm upgrade blast radius** — CLI flags and YAML keys have shifted
  across mlx-lm versions; the three-way command fallback in
  `_build_mlx_lora_cmd()` helps, but Phase P needs a regression run on the
  current default model (Qwen 2.5 3B) before widening the catalog.
- **Gemma 4 multimodality** — E2B/E4B are text+vision+audio; we fine-tune
  text-only. Confirm text-only LoRA leaves the vision/audio towers frozen
  and that GGUF export of the text tower works for the Ollama serving path.
- **Gemma 27B expectations** — it "fits" at batch 1 but throughput will be
  painful on 36 GB; treat 12B/E4B as the practical Mac ceiling and route
  27B+ jobs to CUDA once Phase Q lands.
- **Memory numbers in section 2 are estimates** until the Phase P
  smoke-test matrix replaces them with measured peaks.
- **DB migration** — `trainer_backend` is additive with a default, so
  `create_all` handles fresh DBs; existing SQLite files need a one-line
  `ALTER TABLE` (or the existing auto-create-on-missing-column pattern).
- **Licensing** — Gemma models ship under the Gemma Terms of Use (gated HF
  repos; `huggingface-cli login` required once per machine).

## References

- [mlx-lm LoRA docs & supported models](https://github.com/ml-explore/mlx-lm)
- [Fine-tuning Gemma 3 with LoRA using MLX-LM](https://www.matt-herold.de/blog/how-to-finetune-an-llm-with-lora-with-mlx-lm/)
- [Fine-Tuning on Mac: LoRA & QLoRA with MLX](https://insiderllm.com/guides/fine-tuning-mac-lora-mlx/)
- [Gemma 4 model overview (Google AI)](https://ai.google.dev/gemma/docs/core)
- [Unsloth Gemma 4 fine-tuning guide (VRAM figures)](https://unsloth.ai/docs/models/gemma-4/train)
- [Fine-tuning Gemma 4 on Apple Silicon + MLX](https://antigravitylab.net/en/articles/antigravity/gemma-4-finetuning-apple-silicon-mlx-guide)
- Internal: `docs/PLAN.md`, `packages/trainer/runner.py`, `apps/api/routers/models.py`, `packages/exporter/pipeline.py`
