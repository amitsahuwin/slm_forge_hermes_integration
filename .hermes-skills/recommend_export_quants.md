# Skill: Recommend Export Quants

Given a trained model + target device, recommend which GGUF quantization level(s) to export.

## Trade-off table

| Quant   | Size (~3B model) | Quality loss | Best for |
|---------|------------------|--------------|----------|
| `F16`   | ~6 GB            | none         | Desktop / reference; debugging quality regressions. |
| `Q8_0`  | ~3 GB            | ~1%          | High-end desktop; production where storage is cheap. |
| `Q5_K_M`| ~2 GB            | ~2-3%        | iPhone Pro / iPad — balanced sweet spot. |
| `Q4_K_M`| ~1.5 GB          | ~3-5%        | iPhone base models, edge devices. |

## Input

- Base model identifier (e.g. `mlx-community/Qwen2.5-3B-Instruct-4bit`).
- Approximate parameter count if known.
- Target device(s): `iphone_base`, `iphone_pro`, `ipad`, `mac_desktop`, `mac_laptop`, `edge_device`.
- Use case: `chat`, `classification`, `code_gen`, `summarization`.

## Output (JSON)

```json
{
  "recommended_quants": ["Q4_K_M", "Q5_K_M"],
  "primary": "Q5_K_M",
  "rationale": "iPhone Pro has ample RAM; Q5_K_M gives near-Q8 quality at iPhone-friendly size.",
  "estimated_sizes_mb": { "Q4_K_M": 1700, "Q5_K_M": 2100, "Q8_0": 3200, "F16": 6000 },
  "warnings": [
    "Classification tasks sometimes regress more sharply at Q4 — verify accuracy on canary before shipping."
  ]
}
```

If unsure about base size, estimate conservatively (assume 3-4B params for any Qwen 2.5 / Gemma 3 base unless told otherwise).
