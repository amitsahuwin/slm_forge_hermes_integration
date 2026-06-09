# Skill: Explain Metric Anomaly

When a training run shows anomalous metric behavior — val/train ratio out of band, loss spikes, NaN, plateau too early — explain the likely cause in plain English and recommend a concrete next step.

## Common anomaly patterns

| Symptom | Likely cause |
|---|---|
| `val_loss > 1.5 * train_loss` after early steps | Overfitting (often dataset too small). |
| `val_loss < 0.9 * train_loss` for many steps | Train/valid leakage, or validation is too easy. |
| `train_loss` spikes mid-run | LR too high; instability; bad batch. |
| `train_loss` flat from start | LR too low, or model already saturated. |
| NaN losses | Numerical instability — too-high LR, mixed-precision issue, or bad input data. |
| `tokens/sec` collapses mid-run | Memory pressure → swap; OS reclaiming RAM. |

## Input

- Run config (method, lr, batch_size, num_layers, iters).
- Time series of `train_loss` + `val_loss` (last ~50 points).
- Optional `canary_loss` if present.

## Output (JSON)

```json
{
  "severity": "info" | "warning" | "critical",
  "anomaly_kind": "overfitting" | "leakage" | "instability" | "underfitting" | "memory_pressure" | "nan" | "none",
  "summary": "1-sentence plain-English description of what's happening",
  "evidence": ["specific data points or trends supporting the diagnosis"],
  "recommended_action": {
    "stop_run": false,
    "config_changes": { "learning_rate": 5e-5, "num_layers": 12 },
    "reasoning": "why this change should help"
  }
}
```

If everything looks healthy, return `anomaly_kind: "none"` with `severity: "info"` and a friendly "all metrics within normal range" summary.
