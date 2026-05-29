# Hermes Skills

Skill markdown files. `make hermes-install-skills` copies these to `~/.hermes/skills/`.

These skills are used by the autoresearch ratchet's Hermes bridge
(`packages/ratchet/hermes_bridge.py`), which executes them against Ollama
(qwen2.5-coder:14b by default). Each `.md` file is one skill = one
system-prompt + JSON output contract.

## Phase 2 skills

- `propose_hyperparam_mutation` — given iteration history, propose next config
- `diagnose_mps_oom` — recognize MPS OOM, suggest fixes
- `select_method_for_task` — recommend LoRA vs DoRA vs full
- `analyze_canary_drift` — Goodhart-style overfitting detection

## Phase 3 (next)

- `ingest_dataset` — given URL/path, detect format, load
