# Skill: Analyze Canary Drift (Goodhart Guardrail)

Canary drift = `|canary_loss - val_loss|`. If it exceeds the session threshold,
the model is plausibly overfitting to the validation set.

## Diagnosis

| Drift | Interpretation |
|---|---|
| < 0.1 | Healthy. Canary and val correlated. |
| 0.1 – 0.3 | Mild divergence. Watch closely. |
| > 0.3 | Likely overfitting. Recommend regularization. |
| > 0.6 | Serious overfitting. Roll back, reduce capacity. |

## Recommended responses

When drift > threshold:
1. Lower LR by 2×
2. Reduce `num_layers` by ~25%
3. Increase regularization (LoRA dropout if exposed)
4. Stop the session if drift > 0.6 and trending up

## Output

```json
{
  "learning_rate": 2.5e-5,
  "num_layers": 12,
  "reasoning": "Canary drift 0.4 indicates val-set overfitting; reduce LR + capacity",
  "expected_outcome": "Drift narrows; val_loss may rise short-term"
}
```
