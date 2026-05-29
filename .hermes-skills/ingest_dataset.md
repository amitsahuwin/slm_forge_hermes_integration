# Skill: Ingest Dataset

Given a source description (URL / file extension / S3 path / arbitrary text),
recommend the right ingestion endpoint and suggest schema mapping.

## Decision table

| Source signal | Endpoint | Notes |
|---|---|---|
| `s3://...` or `*.amazonaws.com/...` | `POST /api/v1/ingest/s3/preview` | needs creds |
| URL ending in `.jsonl`, `.ndjson`, `.csv`, `.json` | `POST /api/v1/ingest/url/preview` | direct download |
| User uploaded a file | `POST /api/v1/ingest/upload/preview` | multipart/form-data |
| Generic web page URL | `POST /api/v1/ingest/scrape/preview` | trafilatura main-content extraction (static HTML only) |

## Schema mapping heuristics

After preview, the API returns `detected_fields`. Common mappings:
- `prompt_field` ← typically: `question`, `prompt`, `instruction`, `input`, `user`, `query`
- `response_field` ← typically: `answer`, `response`, `output`, `completion`, `assistant`, `content`

For HuggingFace `alpaca`-style datasets: `instruction` + `output`.
For `oasst`/`sharegpt`-style: requires per-turn flattening (not supported by this skill — tell user to preprocess).

## Output (JSON)

```json
{
  "endpoint": "/api/v1/ingest/url/preview",
  "prompt_field_guess": "question",
  "response_field_guess": "answer",
  "template_guess": "qwen",
  "notes": "1-sentence explanation"
}
```
