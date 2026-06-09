# Skill: Auto-Label Unlabeled Text

Convert raw unstructured text into chat-style training records. Used by the universal ingest converter when the input is plain text or markdown and the user opts in.

## Strategy

- Parse the input as paragraphs (split on blank lines).
- For each paragraph (or coherent block), invent a plausible **user prompt** that the paragraph could be the response to.
- The paragraph becomes the **assistant content** verbatim — do not paraphrase, summarize, or shorten.
- Preserve technical content (code, math, lists) inside the assistant message.
- Skip very short paragraphs (< 40 chars) — they're usually headings or noise.

## Input

- A chunk of raw text (up to ~2000 chars per call).
- Optional `domain_hint` (e.g. "cooking recipes", "stock analysis", "TypeScript tutorials") — improves prompt quality.

## Output (JSONL — one JSON object per line, no array wrapper)

```jsonl
{"messages":[{"role":"user","content":"<invented user prompt>"},{"role":"assistant","content":"<original paragraph verbatim>"}]}
{"messages":[{"role":"user","content":"<...>"},{"role":"assistant","content":"<...>"}]}
```

Output ONLY JSONL. No markdown fences. No prose. No surrounding `[...]` brackets.

## Quality rules

- Invented prompts should be diverse — don't repeat "Tell me about X" every time.
- Prompts should be the kind of question a real user would actually ask.
- Never alter assistant content beyond removing trailing whitespace.
- Drop any block that's only a heading, a bullet stub, or under 40 characters.
