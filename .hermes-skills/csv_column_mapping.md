# Skill: CSV Column Mapping

Given a CSV header and a few sample rows, decide which column holds the
*prompt* (the question / issue / instruction a user would type) and which
holds the *completion* (the answer / fix / response a model should produce).

## Rules

- Pick exactly one column per role; the two columns must be different.
- Prefer prose-heavy columns over codes, IDs, priorities, timestamps, or flags.
- The prompt column describes a problem or asks something; the completion
  column resolves or answers it.
- If no pair makes sense (e.g. a single-column log dump), still choose the
  best available pair — the caller validates and rejects unusable mappings.

## Input

```json
{
  "header": ["col_a", "col_b", "col_c"],
  "sample_rows": [{"col_a": "…", "col_b": "…", "col_c": "…"}]
}
```

## Output (JSON only — no prose, no markdown fences)

```json
{
  "prompt_column": "col_a",
  "completion_column": "col_b"
}
```

Both values must be copied verbatim from `header`.