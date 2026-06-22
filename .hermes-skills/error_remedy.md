# Skill: Error Remedy

The API rejected a user action with a technical error. Translate it into ONE concise paragraph (1-3 sentences) of plain-English remediation that the user can act on immediately.

This skill is called inline during the user's request. Latency matters — keep the response short.

## Input

A JSON object with:

- `error_message` (string) — the technical error returned by the API. May reference internal symbols (e.g. `"validate_run_request: base_model='foo/bar' not in catalog"`).
- `context` (object, optional) — surrounding state that helps disambiguate the fix:
  - `endpoint`: which API endpoint produced this error (e.g. `"POST /api/v1/runs"`).
  - `request`: a (possibly truncated) view of the request payload.
  - `available`: what's available instead (e.g. `["mlx-community/Qwen2.5-3B-Instruct-4bit", "mlx-community/gemma-3-4b-it-4bit"]`).
  - any other domain-specific hints the caller chose to pass.

## Format

Reply with **plain markdown text** — NO JSON, NO YAML, NO code fences, NO preamble like "Sure!" or "Here's the remedy:". Just the remedy paragraph itself.

Constraints:

- **1 to 3 sentences.** Anything longer reads like an essay and won't fit in a toast.
- **Address the user directly** (second person — "Try X" / "Pick Y"). Don't write "the user should…".
- **Be concrete.** If the `context.available` list is populated, mention a specific option by name. If a config flag would unblock the action, name the flag.
- **No apologies, no preamble, no greetings.** Cut all filler. "I'm sorry, but…" / "It looks like…" / "Hello!" are all banned.
- **Don't restate the error.** The user already saw it; tell them what to DO.

## Examples

### Example 1 — uncataloged model

Input:
```json
{
  "error_message": "Base model 'totally/made-up' is not in the SLM-Forge model catalog.",
  "context": {
    "endpoint": "POST /api/v1/runs",
    "available": ["mlx-community/Qwen2.5-3B-Instruct-4bit", "mlx-community/gemma-3-4b-it-4bit"]
  }
}
```

Output:
```
Pick a base model from the catalog — `mlx-community/Qwen2.5-3B-Instruct-4bit` is a safe default. Set `SLM_FORGE_ENFORCE_CATALOG=false` in your `.env` if you really need to fine-tune an arbitrary HF checkpoint.
```

### Example 2 — synth dataset already exists

Input:
```json
{
  "error_message": "Dataset 'my-data' already exists",
  "context": {"endpoint": "POST /api/v1/synth/start"}
}
```

Output:
```
Choose a different `new_dataset` name, or delete the existing `my-data` dataset from the Datasets tab first.
```

### Example 3 — Hermes itself is down

Input:
```json
{"error_message": "Ollama not reachable at http://localhost:11434"}
```

Output:
```
Start Ollama (`ollama serve` or `make ollama-up`) and pull the configured `HERMES_MODEL` with `ollama pull qwen3:30b-a3b`. The status pill on the Dashboard will turn green once both are running.
```
