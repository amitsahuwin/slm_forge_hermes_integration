# Skill: Data Quality Review

Given a sample of records from a dataset, identify quality issues and propose fixes.

## What to look for

- **Duplicates** — exact or near-duplicate records (semantic overlap, paraphrases).
- **Length outliers** — records whose token/char length is far outside the bulk distribution.
- **Format inconsistency** — some records have `messages`, others `text`, others `prompt`/`completion`.
- **Empty / placeholder content** — `"TODO"`, `"<placeholder>"`, empty assistant responses.
- **Off-topic** — records that don't match the dataset's stated domain.
- **Prompt-template mismatches** — system prompts that contradict the dataset purpose.
- **Sensitive content** — PII, credentials, secrets accidentally included.

## Severity scale

| Severity | Meaning |
|---|---|
| `low`    | Cosmetic — fix when convenient. |
| `medium` | Will slightly degrade fine-tune quality. |
| `high`   | Likely to derail the fine-tune; fix before training. |

## Output (JSON)

```json
{
  "overall_health": "good" | "fair" | "poor",
  "summary": "1-2 sentence overall assessment",
  "issues": [
    {
      "severity": "high" | "medium" | "low",
      "kind": "duplicates" | "length_outlier" | "format_mismatch" | "empty" | "off_topic" | "pii" | "template_mismatch" | "other",
      "description": "what the issue is",
      "affected_count": 12,
      "fix": "concrete action to take"
    }
  ],
  "ready_to_train": true
}
```

Be honest: if a dataset is too small to assess (< 8 examples) say so in `summary` and set `overall_health = "fair"`.
