# Skill: Model Selection

Given a dataset + task description, recommend which base model to fine-tune.

## Candidate matrix (Apple Silicon, MLX-LM ecosystem)

| Model | Size | Strengths | Trade-offs |
|---|---|---|---|
| `mlx-community/Qwen2.5-3B-Instruct-4bit` | 3B / 4-bit | Strong general reasoning, fast on M-series, low RAM (~3 GB). | English-leaning; coding weaker than 7B+. |
| `mlx-community/Qwen2.5-7B-Instruct-4bit` | 7B / 4-bit | Better reasoning + multilingual. | ~6 GB RAM, ~2× training time. |
| `mlx-community/Llama-3.2-3B-Instruct-4bit` | 3B | Best instruction-following at this size; reliable. | Restrictive license; weaker code. |
| `mlx-community/gemma-3n-E2B-it-bf16` | 2B / bf16 | Tiny, runs on every Mac; good for prototyping. | Quality ceiling is low. |
| `mlx-community/Mistral-7B-Instruct-v0.3-4bit` | 7B / 4-bit | Solid general-purpose; permissive license. | Slightly older; weaker on code/math. |

## Decision rules

- **Code / structured output** → Qwen 2.5 7B Coder if you have headroom; else Qwen 2.5 3B.
- **Persona / style transfer with small dataset (<200 examples)** → Qwen 2.5 3B (LoRA).
- **Multilingual** → Qwen 2.5 (3B or 7B); avoid Llama-3.2 unless english-only.
- **iPhone target** → 3B max; smaller is better.
- **Quick prototype on a small Mac** → Gemma 3n E2B.

## Input

- Dataset name + description + training record count.
- Task description (1-2 sentences).
- Target device (`iphone`, `mac_laptop`, `mac_desktop`).
- Optional: any explicit constraint (license, language, max size).

## Output (JSON)

```json
{
  "primary": "mlx-community/Qwen2.5-3B-Instruct-4bit",
  "alternatives": [
    "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "mlx-community/Qwen2.5-7B-Instruct-4bit"
  ],
  "reasoning": "Small persona-transfer task with 20 training examples on iPhone target → Qwen 2.5 3B 4-bit balances quality and on-device footprint.",
  "expected_iphone_size_gb": 1.7
}
```

Always include at least 1 alternative. Prefer `mlx-community/` namespaces (they're MLX-converted and load instantly).
