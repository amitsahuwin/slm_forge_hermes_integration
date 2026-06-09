# Skill: Synthesize Style Prompt

Given a sample of training records, produce a concise style-guidance string that the dataset-synthesis engine will inject into its generation prompt.

## Goal

Generic synthesis prompts produce generic outputs. A dataset-specific style prompt — derived from the actual training rows — keeps synthesized records on-distribution.

## What to capture

- **Voice & tone** — formal vs casual, terse vs verbose, first vs third person.
- **Structure** — how user inputs are framed; how assistant answers open; consistent endings.
- **Domain vocabulary** — typical entities, units, jargon used.
- **Constraints** — what NOT to do (refusals, format restrictions, length bounds).

## Input

- Dataset name + description.
- 5-10 training records as JSON.

## Output (JSON)

```json
{
  "style_summary": "1-2 sentences capturing the dataset's overall voice",
  "voice": "terse" | "verbose" | "neutral" | "formal" | "casual" | "technical" | "conversational",
  "typical_length_chars": { "user": 80, "assistant": 200 },
  "do": [
    "Always start assistant turns with a direct answer, then explanation",
    "Use precise financial terminology (P/E, EPS, etc.)"
  ],
  "do_not": [
    "Don't hedge with phrases like 'I think' or 'it depends'",
    "Don't include disclaimers"
  ],
  "style_guidance": "Concatenated single-paragraph prompt fragment ready to drop into the synthesizer."
}
```

The `style_guidance` field is the actual string the synthesizer will use — make it ~3-5 sentences, actionable, no JSON syntax, no markdown.
