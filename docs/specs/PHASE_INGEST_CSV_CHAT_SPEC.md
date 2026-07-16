# Spec — Ingest-time CSV cleaning + chat-format conversion

**Status:** approved · 2026-07-16
**Motivation:** Run 33 trained on a corrupted CSV ingest (`sx-100rows`): unmapped columns
were dumped as `{"text": "col: val\n..."}` blobs, corrupted rows (misaligned multi-line
cells, Python-list reprs) passed with zero validation, and the resulting model emitted
gibberish. See `docs/PLAN.md` build log.

## Scope

Every **CSV** ingest — any column/row count, on **all** ingest paths (sync `/file`,
`/url`, `/scrape`, `/s3`, `/preview`, and the async large-file job) — is cleaned and
converted to chat format **before** the dataset is saved.

Non-goals: non-CSV formats (JSONL/markdown/plain-text/Ollama paths unchanged);
fixing corruption *inside* a cell (unsalvageable rows are dropped, not repaired);
per-column user mapping UI.

## Behavior

### 1. Column mapping (tiered, unattended)

`resolve_mapping(header, samples, *, hermes_resolver)` in
`packages/dataset_ingest/csv_chat.py`:

1. **Heuristic:** normalize names (lowercase, strip `_`/spaces/BOM). Prompt-ish hints:
   `prompt|instruction|question|input|user|query|issue|problem|desc`; completion-ish:
   `completion|response|answer|output|assistant|reply|fix|solution|resolution`.
   A column qualifies only if its name matches a hint. On multiple candidates per
   side, prefer the most text-heavy (avg sample value length). Succeeds when it finds
   one distinct (prompt, completion) pair.
2. **Hermes:** otherwise call `hermes_resolver(header, samples≤5)` → strict JSON
   `{"prompt_column", "completion_column"}`; both must exist in header and differ.
3. **Failure:** Hermes unavailable / invalid / unresolvable → `MappingError` with an
   actionable message ("start Ollama or rename columns to prompt/completion").
   **No silent fallback.** The `{"text"}` column-dump fallback is removed for CSV.

### 2. Row cleaning (`RowCleaner`)

Applied to the mapped (prompt, completion) values; drop reasons are counted:

| reason | rule |
|---|---|
| `empty` | either field empty after strip, or < 3 chars |
| `list_repr` | field is a Python list repr (`re.fullmatch(r"\[\s*(['\"]).*\1\s*\]", v, re.S)` confirmed by `ast.literal_eval` → list) |
| `control_chars` | field contains `[\x00-\x08\x0b\x0c\x0e-\x1f]` |
| `duplicate` | sha256 16-byte digest of normalized (prompt, completion) already seen |

Field-count-vs-header mismatches remain dropped by the row parsers (existing behavior).

### 3. Output format

`{"messages": [{"role": "user", "content": <prompt>}, {"role": "assistant",
"content": <completion>}]}` — trainers auto-detect chat and set `mask_prompt=true`.

### 4. Thresholds & reporting

- Drop counts per reason go to the dataset README ("Conversion notes") and preview
  `warnings` / `drop_reasons`.
- If **> 50%** of data rows are dropped → ingest fails (`DropThresholdError` → HTTP 400
  on sync; job `failed` on async — async reuses the existing `_MAX_DROPPED_RATIO` gate).
- Preview response adds `column_mapping`, `dropped_rows`, `drop_reasons`.

### 5. Streaming constraints (async path)

`iter_csv_chat_records` buffers ≤ 50 raw rows to resolve the mapping (Hermes needs
header + samples up front), then streams record-by-record. RAM stays bounded:
fixed-size buffer + 16-byte dedupe digests (~16 MB per million rows, documented).

## Interfaces

```python
# packages/dataset_ingest/csv_chat.py
@dataclass(frozen=True)
class ColumnMapping: prompt_col: str; completion_col: str; method: Literal["heuristic", "hermes"]
class MappingError(ValueError): ...
class DropThresholdError(ValueError): ...
HermesResolver = Callable[[list[str], list[dict[str, str]]], dict]

def resolve_mapping(header, samples, *, hermes_resolver=None) -> ColumnMapping
class RowCleaner:
    def __init__(self, mapping: ColumnMapping): ...
    def clean(self, row: dict) -> dict | None      # chat record or None (counted)
    @property
    def stats(self) -> CleanStats
@dataclass
class CleanStats:
    kept: int; dropped: dict[str, int]             # reason -> count
    def total_dropped(self) -> int
    def check_threshold(self, max_ratio: float = 0.5) -> None   # DropThresholdError
    def readme_lines(self) -> list[str]
    def warnings(self) -> list[str]
def default_hermes_resolver(header, samples) -> dict  # run_skill("csv_column_mapping")

# packages/dataset_ingest/converter.py
def csv_to_chat(text: str, *, hermes_resolver=None) -> tuple[list[dict], ColumnMapping, CleanStats]

# packages/dataset_ingest/streaming.py
async def iter_csv_chat_records(chunks, *, hermes_resolver=None, sample_size=5, buffer_max=50)
    -> AsyncIterator[ParsedLine]   # records are {"messages": [...]}; MappingError may raise
```

## Failure modes

| condition | sync | async job |
|---|---|---|
| unmappable header + Hermes down/invalid | HTTP 400, actionable message | job `failed`, same message |
| > 50% rows dropped | HTTP 400 with ratio + reasons | existing `_MAX_DROPPED_RATIO` gate fails before publish |
| headerless / single-column CSV | HTTP 400 (no pair to map) | job `failed` |

## Acceptance criteria

1. The corrupted-fixture CSV ingests with corrupted rows dropped and reported; output
   rows are all valid chat records.
2. `issue_description,fix_provided` header maps heuristically — no Hermes call.
3. Ambiguous header triggers the resolver exactly once; Hermes-down → clear 400/failed.
4. Garbage-majority CSV refuses to publish (400 / failed job).
5. Both sync and async paths produce identical record shapes for the same input.
6. All existing non-CSV ingest behavior unchanged; full test suite green.