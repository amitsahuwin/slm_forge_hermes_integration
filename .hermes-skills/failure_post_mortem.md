# Skill: Failure Post-Mortem

A run failed. Produce a thorough post-mortem in markdown — useful both as a chat reply AND as a saved artifact at `runs/<id>/post_mortem.md`.

This is BROADER than `diagnose_mps_oom` — that skill handles only memory-pressure failures. Use this skill for any failure: import errors, data shape mismatches, MLX version skew, exit code N from the trainer subprocess, etc.

## Input

- Run config (method, base_model, batch_size, lr, num_layers, etc.).
- Recorded `error_message`.
- Tail of `training.log` (last ~80 lines).
- Optional: previous run that succeeded with the same dataset (for diff context).

## Format

Output a markdown body with these sections (in order):

```
# Run #{run_id} — Post-mortem

## Summary
1-2 sentences naming the root cause.

## What happened
Chronological reconstruction from the log. Cite specific log lines.

## Root cause
The single most likely underlying issue.

## Fix
Concrete next step, with exact config delta or commands.

## Prevention
What to change in workflow / defaults to avoid this class of failure.
```

End with a fenced JSON block:

```json
{
  "root_cause": "short tag — one of: oom | shape_mismatch | import_error | dataset_format | mlx_version | trainer_crash | other",
  "confidence": "high" | "medium" | "low",
  "config_delta": { /* optional: any specific config field changes */ },
  "rerun_safe": true
}
```

Be honest: if the log tail is uninformative, say so in `Summary` and lower `confidence`.
