# SLM-Forge Market Analysis

_Last updated: 2026-06-06_

## 1. Executive Summary

SLM-Forge occupies a narrow but defensible slice of the LLM fine-tuning market: a local-first, Apple Silicon-native lab that takes a single user from raw dataset to a quantized GGUF running offline on an iPhone, with a Hermes-agent-driven autoresearch loop doing the hyperparameter sweeps in between. The moat is the combination, not any single feature. LlamaFactory has a Gradio UI but no Apple Silicon story and no mobile pipeline. Unsloth Studio is now a no-code local trainer but does not target Mac unified memory as its first-class path. mlx-lm-lora and mlx-tuning-fork cover the MLX training core but ship as CLIs with no UI, no agentic loop, and no end-to-end iPhone export. The autoresearch ratchet, driven by a local Ollama model through a Hermes bridge, is genuinely unusual: most competitors either expose hand-rolled hyperparameter sweeps (W&B Sweeps, mlx-tuning-fork) or auto-tune in a closed cloud (Predibase, Together) without an agent reasoning over the search.

Where SLM-Forge honestly lags is breadth. It does not do RLHF PPO, multi-GPU, full distillation pipelines, or VLM fine-tuning. It has no model registry or team features, no managed inference endpoints, and no synthetic-data factory comparable to Unsloth's or mlx-lm-lora's teacher-model batch generation. It also has no eval harness comparable to W&B's LLM evals or the LM Evaluation Harness integrations that LlamaFactory and Axolotl wire in. The strategic question is not whether SLM-Forge can match these on breadth — it cannot — but whether it can be the unambiguous best tool for the "fine-tune on my MacBook, ship to my iPhone" workflow. The rest of this document scopes that question.

## 2. Competitive Landscape

### LlamaFactory (hiyouga/LLaMA-Factory)
LlamaFactory is the most adopted open-source fine-tuning framework on GitHub, with 70k+ stars and adoption by Amazon, NVIDIA, and Aliyun. It supports 100+ LLMs and VLMs with a wide training menu: full fine-tuning, freeze-tuning, LoRA, 2–8 bit QLoRA via AQLM/AWQ/GPTQ/HQQ/EETQ, plus DPO, ORPO, SimPO, KTO and PPO RLHF. It ships LlamaBoard (a Gradio web UI) and an OpenAI-compatible HTTP API. Advanced optimizer and adapter algorithms — GaLore, BAdam, APOLLO, Adam-mini, Muon, OFT, DoRA, LongLoRA, LLaMA Pro, MoD, LoRA+, LoftQ, PiSSA — are first-class. Target user is the researcher or applied engineer with a Linux box and at least one CUDA GPU; Apple Silicon is not its battleground. It is what SLM-Forge looks like at 100× the surface area, minus on-device deployment.

### Axolotl (axolotl-ai-cloud)
Axolotl wraps HuggingFace Transformers, PEFT, TRL, and DeepSpeed behind a single YAML config. Its differentiator is config-driven reproducibility: one file describes model, dataset, training method, and hyperparameters. Recent additions include GRPO support, Reward Modelling / Process Reward Modelling, MoE expert quantization to cut VRAM, multimodal fine-tuning, and LoRA optimizations across DDP and DeepSpeed. It supports Mistral Small 4, Qwen3.5, Qwen3.5 MoE, GLM-4.5/4.6/4.7 families. Target user is the practitioner running multi-GPU jobs who values YAML reproducibility over a UI. It has no native Apple Silicon path and no GUI.

### Unsloth
Unsloth has built its position on raw speed and memory efficiency: custom kernels claiming up to 2× faster training with up to 70% less VRAM, supporting 500+ models across text, vision, audio, and embeddings. It covers LoRA, QLoRA, full fine-tuning, FP8 training, and a broad RL menu (DPO, ORPO, GRPO, GSPO) plus distillation. Unsloth Studio, launched March 2026, is a no-code local web UI for fine-tune, run, and export on local hardware. The catch for SLM-Forge: Unsloth's local-hardware story still assumes NVIDIA. Apple Silicon performance is not where their kernels were tuned.

### H2O LLM Studio
H2O LLM Studio is a no-code GUI specifically aimed at users without Python experience. It supports LoRA and 8-bit training, experiment monitoring, side-by-side experiment comparison, a chat playground for instant feedback, and one-click push to Hugging Face. H2O recently removed RLHF in favor of DPO/IPO/KTO. Target user is the enterprise data team that wants a clickable on-prem GUI without writing training code. It is Linux-first with CUDA expected, and aimed at single-server enterprise deployments rather than laptops.

### Weights & Biases Sweeps + W&B Models
W&B is the experiment-tracking incumbent. Sweeps automate hyperparameter search via grid, random, and Bayesian methods, log fine-tune jobs alongside evals in the same project, and let teams promote the best checkpoint to production. W&B Models is a centralized registry with CI/CD automations and lineage. Artifacts version datasets and checkpoints together. Target user is the ML team that already runs training somewhere else and needs the system-of-record. W&B does not itself train models — it observes whoever does. SLM-Forge could integrate with W&B rather than compete with it.

### Together AI fine-tuning
Together is a managed cloud offering SFT and DPO via LoRA or full fine-tuning across every Llama, Mistral, and Qwen size up to 405B. LoRA fine-tuning is priced around $0.48 per million tokens for models up to 16B and $14 per million for Llama 70B. Inference runs on shared serverless endpoints at standard rates plus a small LoRA overhead, or on dedicated H100/B200 hours. Target user is the team that wants a checkpoint without owning hardware. The trade-off is the opposite of SLM-Forge: zero local control, zero offline path, but trivial scale.

### Modal Labs fine-tuning templates
Modal ships an open-source LLM fine-tuning template demonstrating DeepSpeed ZeRO-3 sharding, LoRA adapters, and Flash Attention, packaged as Modal functions. It is not a product so much as a reference scaffold for users who want serverless GPU training without operating a cluster. Target user is the Python developer who wants reproducible cloud training scripts. Modal is complementary to, not competitive with, an on-device tool — but it sets the expectation for "spin up a fine-tune from a config in minutes" that local tools are measured against.

### MLX Tuning Fork (chimezie/mlx-tuning-fork)
The closest direct analogue to the SLM-Forge training core. Built on mlx_lm and OgbujiPT, it adds composable YAML configs, hyperparameter sweeping, input/prompt masking, and the ability to chain training steps (e.g., domain-adaptive pretraining followed by instruction tuning). Supports (Q)LoRA and (Q)DoRA. Target user is the Apple Silicon researcher who is happy in a terminal. No UI, no agent loop, no export pipeline — but the YAML composability is a feature SLM-Forge does not currently match.

### mlx-lm-lora (Goekdeniz-Guelmez/mlx-lm-lora)
The other serious Apple Silicon training library. Supports LoRA, DoRA, and full fine-tuning, plus Quantization-Aware Training for SFT, DPO, and ORPO at 4–16 bit. It ships Dr. GRPO and DAPO RL methods, a PPO-style loop with a reward model, synthetic data generation via teacher-model batch generation for distillation, fused-model upload to HuggingFace Hub, and GGUF export. This is the library that, feature-for-feature, comes closest to what SLM-Forge offers under the hood — and arguably exceeds it on RL methods and synthetic data. The gap SLM-Forge fills is the orchestration, UI, agentic search, and iPhone deploy story around such a library.

### OpenPipe
OpenPipe historically pitched "turn expensive prompts into cheap fine-tuned models" and pivoted hard into RL for agents (Agent Reinforcement Trainer / ART, Serverless RL with GRPO). It targets product teams that want continuous improvement from production traffic, with live dashboards, guardrails, PII redaction, SOC 2 / HIPAA / GDPR, and on-prem deployment. Important platform note: OpenPipe training and inference are migrating to W&B, with the legacy platform sunsetting new training on July 30, 2026. Target user is the agent-product team that wants RL loops on live data — orthogonal to SLM-Forge's single-user lab.

### Predibase / Ludwig
Predibase commercializes the open-source Ludwig framework. Ludwig is a declarative, low-code framework with PEFT, QLoRA, automatic batch sizing, gradient checkpointing, and RoPE scaling for long context. Predibase is now part of Rubrik and offers serverless reinforcement fine-tuning following the DeepSeek-R1 wave. Target user is the enterprise team that wants adapter-based fine-tuning as a managed service with HuggingFace push-button integration. Again, opposite end of the spectrum from SLM-Forge: cloud-managed, multi-tenant, no offline story.

## 3. Feature Matrix

Legend: check = full support, x = absent, ~ = partial / via plugin / uncertain.

| Feature | SLM-Forge | LlamaFactory | Axolotl | Unsloth | H2O LLM Studio | Together | W&B |
|---|---|---|---|---|---|---|---|
| LoRA / DoRA | check | check | check | check | check (LoRA) | check | n/a |
| Full SFT | check | check | check | check | check | check | n/a |
| DPO / ORPO / KTO | ~ (DPO only) | check | check | check | check | ~ (DPO) | n/a |
| RLHF PPO | x | check | ~ (via TRL) | x | x | x | n/a |
| GRPO / agent RL | x | ~ | check | check | x | x | n/a |
| Evals harness | x | check | ~ | ~ | ~ | x | check |
| Dataset preview UI | check | check | x | check (Studio) | check | check | x |
| Playground / chat UI | ~ | check | x | check (Studio) | check | check | x |
| Model registry / leaderboard | x | ~ | x | x | ~ | check | check |
| Multi-GPU / cluster | x | check | check | check | check | check | n/a |
| Apple Silicon native | check | x | x | x | x | x | n/a |
| Cost / ETA estimator | ~ | x | x | x | x | check | x |
| Mobile / on-device export (GGUF to phone) | check | ~ (GGUF only) | ~ (GGUF only) | check (GGUF) | x | x | x |
| Synthetic data generation | x | ~ | x | check | x | x | x |
| Agentic autoresearch loop | check | x | x | x | x | x | x |
| Plugin / skill system | ~ | x | ~ (YAML) | x | x | x | check |
| Local-first (no cloud required) | check | check | check | check | check | x | x |
| Multi-user / team | x | x | x | x | ~ | check | check |

## 4. Where SLM-Forge Wins Today

SLM-Forge is the only tool in this comparison that takes one user from CSV upload to a quantized GGUF on their iPhone, end-to-end on a single MacBook, with an agent loop choosing the hyperparameters. Every other tool either lives on NVIDIA (LlamaFactory, Axolotl, Unsloth, H2O), in the cloud (Together, Modal, Predibase, OpenPipe), or stops at the training step with no mobile deploy and no agent in the loop (mlx-tuning-fork, mlx-lm-lora). For a developer whose hard constraint is "my data never leaves this laptop and the result has to run on my phone in airplane mode," SLM-Forge is the path of least resistance. That is a small but real audience: privacy-sensitive solo developers, indie iOS app makers wanting offline LLM features, security researchers, and anyone in a regulated environment that disallows cloud training.

## 5. Top 10 Gaps with Prioritized Recommendations

1. **Eval harness with side-by-side run comparison.** Who has it: LlamaFactory, H2O, W&B, Unsloth Studio. SLM-Forge runs training but lacks a structured way to score a fine-tune against a held-out set or against the base model. Implementation: wrap `lm-evaluation-harness` as a Huey job, persist eval scores per run in SQLite, and add a `/runs/compare` UI that diffs two runs across the same eval suite. **Priority: P0. Effort: M.**

2. **Synthetic data generation via local teacher model.** Who has it: Unsloth, mlx-lm-lora. The Hermes bridge already talks to Ollama — that is the teacher. Implementation: a `/datasets/synthesize` flow that takes a seed prompt set, fans out batched generations through Ollama, and writes a new dataset row-by-row with provenance metadata. **Priority: P0. Effort: M.**

3. **DPO/ORPO/KTO preference training.** Who has it: LlamaFactory, Axolotl, Unsloth, mlx-lm-lora, H2O. SLM-Forge only does SFT plus partial DPO. mlx-lm-lora ships ORPO and QAT-DPO out of the box. Implementation: import mlx-lm-lora as a dependency and add a "preference" job type to the trainer worker; reuse the existing ratchet for preference-pair sweeps. **Priority: P0. Effort: M.**

4. **Hugging Face Hub push from the UI.** Who has it: H2O, Ludwig, mlx-lm-lora, basically everyone. Currently SLM-Forge has GGUF export but no remote model registry hand-off. Implementation: add a `huggingface_hub` token to settings, a "Push to Hub" button on the runs page, and a fused-weights upload step. **Priority: P1. Effort: S.**

5. **YAML/declarative recipe system.** Who has it: Axolotl, mlx-tuning-fork, Ludwig. The UI is great for one-off jobs, but power users want to commit an experiment to git. Implementation: define an `slmforge.yaml` schema that covers dataset, model, training method, and ratchet config; let `slmforge run recipe.yaml` execute it headless. **Priority: P1. Effort: M.**

6. **Run leaderboard and model registry per project.** Who has it: W&B Models, Unsloth Studio, H2O. SLM-Forge already has runs in SQLite but lacks a leaderboard view. Implementation: a `/projects/:id/leaderboard` page that sorts runs by chosen eval metric, marks the current champion, and pins the GGUF export of the champion to a "current" symlink. **Priority: P1. Effort: S.**

7. **Cost and time estimator before launch.** Who has it: Together (cost), partial elsewhere. SLM-Forge owns the metal, so the estimator should be in wall-clock minutes and Wh, not dollars. Implementation: a small calibration table from past runs on the same machine; surface "this experiment will take ~42 min and ~28 Wh" before the user clicks start. **Priority: P1. Effort: S.**

8. **Vision and audio model fine-tuning.** Who has it: Axolotl, Unsloth, LlamaFactory (VLMs). mlx-lm already supports vision-capable Qwen and Llama variants. Implementation: extend the dataset ingestor to handle image-text pairs and add a VLM model card type. **Priority: P2. Effort: L.**

9. **Distillation pipeline (big teacher to small student, on device).** Who has it: Unsloth, mlx-lm-lora. Natural fit for the local Ollama + Hermes architecture — qwen3:30b-a3b is already running. Implementation: a "distill" experiment type where the teacher is the running Ollama model and the student is a 3B target, sharing the synthetic-data generator from gap #2. **Priority: P2. Effort: M.**

10. **Public-facing skill/plugin SDK for the Hermes agent.** Who has it: nobody, really — this is whitespace. Hermes already drives the ratchet; expose its tool interface so users can write new "research strategies" as Python plugins. Implementation: define a `RatchetStrategy` protocol, ship 2-3 built-ins (Bayesian, bandit, agentic-LLM), and document the SDK. **Priority: P2. Effort: M.**

## 6. Strategic Recommendation: Don't Try to Win Where You Can't

SLM-Forge will lose any fight where the rules are "more GPUs, more parameters, more tenants." LlamaFactory has 70k stars and Amazon-scale users; matching its surface area is a five-engineer-year proposition and the win condition is unclear. Cluster scale, RLHF PPO with reward-model serving, multi-tenant team features, and managed inference endpoints are all moats that belong to other tools. Trying to bolt them onto a single-user MacBook product makes the product worse, not better.

The on-device story, however, is genuinely uncontested. Unsloth Studio is local but assumes CUDA. LlamaFactory's local UX is a Gradio panel, not a polished React app. mlx-lm-lora is the strongest Apple Silicon competitor at the training-library layer but ships no UI and no end-to-end deploy. The win condition for SLM-Forge is to be the obvious answer to "I want to fine-tune a small model on my Mac and run it on my iPhone." Every roadmap decision should be filtered through that lens. Eval harness, yes — because you can't ship a phone model you haven't measured. Synthetic data, yes — because the teacher already lives on the laptop. Multi-GPU DeepSpeed, no — wrong product. RLHF PPO with a reward server, no — wrong product. Hosted inference, no.

The secondary play is the autoresearch agent. Hermes-driven ratchet is differentiated and naturally extends to a published "skill" SDK where the community contributes research strategies. That is harder to copy than any single training feature, and it slots cleanly under the on-device positioning rather than competing with it.

## 7. Suggested Roadmap (Q1–Q4 2026 from current date forward)

### Q3 2026 — Measure what you ship
Deliver gap #1 (eval harness with side-by-side compare) and gap #7 (cost/ETA estimator). The product currently can train but cannot tell the user whether training helped. Closing this loop is the highest-leverage move and unblocks the leaderboard work in Q4. Also deliver gap #4 (Hugging Face push) as a small wedge that signals the product is serious about reproducibility.

### Q4 2026 — Local data factory
Deliver gap #2 (synthetic data generation via Ollama) and gap #3 (DPO/ORPO via mlx-lm-lora integration). Together these turn SLM-Forge from "fine-tune what you have" into "generate what you need and align it locally." Gap #6 (leaderboard) lands here because evals from Q3 now have something to rank.

### Q1 2027 — Recipes and reproducibility
Deliver gap #5 (YAML recipe system) and gap #10 (Hermes skill/plugin SDK). At this point the product has a sharp story for individuals; the recipe + plugin layer is what lets the community extend it without merging code. Publish 5–10 recipes alongside the existing 6 starter datasets.

### Q2 2027 — Beyond text
Deliver gap #8 (VLM fine-tuning) and gap #9 (distillation). These are the bigger bets that depend on the foundations laid in Q3–Q1. VLM in particular opens the iPhone-deployment story to a new class of apps (on-device document understanding, offline visual assistants).

## 8. Appendix: Research Methodology and Sources

Each competitor was verified via web search for current (2025–2026) feature claims rather than relying on assistant priors. Where a search returned uncertain information (notably Modal Labs' 2026-specific changes), the relevant feature is marked partial in the matrix. Where a 2026 release date is explicitly cited in sources (Unsloth Studio March 2026, OpenPipe-to-W&B migration July 2026), that is reflected in the prose.

Verified URLs:

- LlamaFactory: https://github.com/hiyouga/LLaMA-Factory and https://github.com/hiyouga/LlamaFactory/releases
- Axolotl: https://axolotl.ai/ and https://github.com/axolotl-ai-cloud/axolotl and https://dev.to/ultraduneai/eval-003-fine-tuning-in-2026-axolotl-vs-unsloth-vs-trl-vs-llama-factory-2ohg
- Unsloth: https://unsloth.ai/ and https://github.com/unslothai/unsloth and https://aiautomationglobal.com/blog/unsloth-studio-no-code-local-llm-finetuning-2026
- H2O LLM Studio: https://github.com/h2oai/h2o-llmstudio and https://docs.h2o.ai/h2o-llmstudio/
- W&B Sweeps and Models: https://docs.wandb.ai/models and https://wandb.ai/wandb_fc/mlops_course/reports/Hyperparameter-Tuning-with-W-B-Sweeps--VmlldzozMjI0NzAz
- Together AI: https://www.together.ai/pricing and https://docs.together.ai/docs/fine-tuning-pricing
- Modal Labs: https://modal.com/blog/llm-fine-tuning-overview
- mlx-tuning-fork: https://github.com/chimezie/mlx-tuning-fork
- mlx-lm-lora: https://github.com/Goekdeniz-Guelmez/mlx-lm-lora and https://github.com/ml-explore/mlx-lm
- OpenPipe: https://openpipe.ai/ and https://docs.openpipe.ai/features/fine-tuning/quick-start
- Predibase / Ludwig: https://predibase.com/ and https://ludwig.ai/latest/user_guide/distributed_training/finetuning/
