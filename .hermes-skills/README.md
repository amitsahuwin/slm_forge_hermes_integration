# Hermes Skills

This directory holds Hermes Agent skills (markdown files) that are version-controlled
in the repo and copied into `~/.hermes/skills/` by `make hermes-install-skills`.

## Phase 2 will add:

- `propose_hyperparam_mutation.md` — given metrics history, propose next config change
- `diagnose_mps_oom.md` — recognize Apple MPS OOM, suggest fixes (batch size, QLoRA)
- `select_method_for_task.md` — recommend LoRA vs QLoRA vs full SFT vs DPO
- `recommend_base_model.md` — Gemma 4 E2B/E4B/26B vs Qwen 2.5 vs Llama 3.2
- `debug_training_error.md` — read traceback, propose fix, write new skill if novel
- `ingest_dataset.md` — given URL/path, detect format, load
- `analyze_canary_drift.md` — detect Goodhart-style overfitting, propose regularization

Each skill is a single `.md` file with a YAML front-matter header describing
when Hermes should invoke it, and a body containing the instruction text.
