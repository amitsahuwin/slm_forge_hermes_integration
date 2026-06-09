# Skill: Propose Canary Set

For a dataset without a canary held-out set, generate 5 canary records that cover edge cases the trainer should NOT overfit to.

## What canary examples should look like

- **In-domain but stylistically distinct** from training records — different phrasing, longer, shorter, more terse.
- **Cover failure modes**: ambiguous inputs, out-of-distribution prompts within the same domain, requests that require refusal, multi-turn or partial inputs.
- **Match the existing record schema** exactly (chat / text / prompt+completion).
- **No leakage**: must NOT duplicate or near-duplicate any training row.

## Input you'll receive

- The dataset name and description.
- A sample of 5-10 training records (same shape as what you should produce).

## Output (JSON)

```json
{
  "canary": [
    { /* one record in the same schema as the training data */ },
    { /* ... 4 more ... */ }
  ],
  "rationale": [
    "1-sentence description of what edge case canary[0] tests",
    "1-sentence description of canary[1]",
    "..."
  ]
}
```

Generate EXACTLY 5 canary records. Match the source schema field-for-field. Do not invent new fields. Output JSON only — no markdown fences, no prose.
