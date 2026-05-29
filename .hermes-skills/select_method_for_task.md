# Skill: Select Fine-Tuning Method

Given a task description and base model, recommend `lora`, `dora`, or `full`.

## Decision rules

- **`lora`** — default. Use unless you have a specific reason not to.
- **`dora`** — when LoRA plateaus and you suspect the rank is the bottleneck. DoRA usually beats LoRA on the same rank for the same compute.
- **`full`** — only for small base models (<2B params) AND when you have ≥5000 training examples AND when LoRA/DoRA have demonstrably failed.

## Task → method shortcuts

| Task type | Default method |
|---|---|
| Persona/style transfer | lora |
| Domain Q&A | lora |
| Code generation | dora |
| Classification head | full (small model only) |
| Instruction following | lora |
| Tool use | dora |

## Output

```json
{
  "method": "lora",
  "num_layers": 16,
  "reasoning": "1-sentence justification"
}
```
