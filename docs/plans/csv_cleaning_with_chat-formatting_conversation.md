Ingest-time CSV cleaning + chat-format conversion                                                                                                                                             
                                                                                                                                                                                             
 Context                                                                                                                                                                                       
                                                                                                                                                                                               
 Run 33 produced gibberish because the ingested dataset (sx-100rows) was garbage: the CSV's columns (issue_description/fix_provided) didn't match the converter's prompt/completion synonym    
 list, so every row was dumped as a raw {"text": "col: val\n..."} blob — including rows already scrambled at source (multi-line cells misaligned across columns, leftover columns serialized   
 as Python-list reprs like : ['TRUE', 'P4', ...]). No content validation existed, so the corruption was saved, trained on, and exported. Training itself was healthy (grad_checkpoint ruled
 out — verified by generation tests).

 Goal: every CSV ingest — regardless of columns/rows — is cleaned and converted to chat {"messages": [...]} format right before the dataset is saved, on all ingest paths. Trainers already
 auto-detect chat format and set mask_prompt=true (packages/trainer/backends/mlx.py:68-106, cuda.py:42-73), so fixing ingestion fixes training + inference alignment end-to-end.

 User-approved decisions:
 1. Column mapping: tiered — expanded heuristics first, Hermes (qwen3 via Ollama) for ambiguous schemas. Unattended.
 2. Bad rows: drop + report per-reason counts; ingest fails if >50% dropped. Hermes down/ambiguous-unresolvable → fail with actionable error (no silent fallback).
 3. Output: chat messages format.

 New shared module — packages/dataset_ingest/csv_chat.py

 One implementation used by both sync and streaming paths:

 - ColumnMapping(prompt_col, completion_col, method: "heuristic"|"hermes")
 - MappingError (unresolvable / Hermes unavailable / invalid response), DropThresholdError
 - resolve_mapping(header, samples, *, hermes_resolver=None) -> ColumnMapping
   - Tier 1 heuristic: normalized synonym match (lowercase, strip _/spaces; prompt-ish: question|issue|problem|prompt|input|desc|query|instruction; completion-ish:
 answer|fix|response|solution|output|completion|resolution|reply). If exactly one distinct pair of text-heavy columns (avg len ≥ 30 over samples) with clear role hints → done.
   - Tier 2: otherwise call hermes_resolver(header, samples) (≤5 sample rows) → validate returned columns exist in header and differ → ColumnMapping(method="hermes"). Any failure →
 MappingError with message "start Ollama or rename columns to prompt/completion".
 - RowCleaner (stateful: dedupe digest set + per-reason counters) → clean(row) -> {"messages":[...]} | None. Drop rules:
   - mapped prompt/completion empty after strip or < 3 chars
   - value is a Python-list repr: re.fullmatch(r"\[\s*(['\"]).*\1\s*\]", v, re.S) confirmed via ast.literal_eval
   - control chars [\x00-\x08\x0b\x0c\x0e-\x1f] in either field
   - exact duplicate (sha256 of normalized (prompt, completion), 16-byte digests)
 - CleanStats.check_threshold(max_ratio=0.5) → raises DropThresholdError; readme_lines(), warnings()
 - default_hermes_resolver() — follows the run_skill pattern in apps/api/services/dataset_qa.py

 New Hermes skill: .hermes-skills/csv_column_mapping.md — input headers + samples, output strict JSON {"prompt_column", "completion_column"} (mirror data_quality_review skill format).

 Integration

 Sync path — apps/api/routers/ingest_v2.py _convert() (:122)

 - For fmt == "csv": call new converter.csv_to_chat(text, hermes_resolver=...) -> (records, ColumnMapping, CleanStats) (thin wrapper: csv.DictReader + Sniffer as today, then csv_chat).
 - Remove the "<8 records → Ollama" fallback for CSV only (:157-165); keep for other formats.
 - MappingError / DropThresholdError → HTTP 400 with the message.
 - Append stats.readme_lines() to write_dataset(conversion_notes=...) (converter.py:531 — param exists).
 - Warnings in response += stats.warnings().

 Async streaming path — packages/dataset_ingest/streaming.py + apps/api/services/ingest_jobs.py

 - New iter_csv_chat_records(chunks, *, hermes_resolver, sample_size=5, buffer_max=50): stream-parse rows as today; buffer first ≤50 raw rows; resolve mapping once (Hermes via
 asyncio.to_thread); instantiate one RowCleaner; flush buffer, then continue record-by-record. Constant RAM preserved (bounded buffer + digest set — documented).
 - _parse_to_staging() (ingest_jobs.py:75) swaps in the new iterator for CSV; cleaner drops flow into the existing dropped counter; the existing _MAX_DROPPED_RATIO fail-before-publish gate
 (:240-249) handles the >50% rule already. MappingError propagates → _mark_failed.
 - _write_readme (:107) += per-reason drop lines + column mapping.

 Preview — ingest_v2.py /preview (:386) + apps/web/src/pages/NewDatasetV2.tsx

 - IngestPreviewResponse += column_mapping: dict | None, dropped_rows: int, drop_reasons: dict[str, int]. Samples now show chat records.
 - Frontend renders mapping ("issue_description → user, fix_provided → assistant"), drop stats, threshold warning.

 Deletions (after migration)

 - csv_row_to_mlx's {"text"} fallback and resolve_csv_mapping (converter.py:225-257) — superseded; for CSV it's mapped-or-fail. Update streaming.py import (:29) and tests.
 - Non-CSV formats (JSONL/Ollama paths) pass through unchanged — scope is CSV only.

 Order of work (TDD — failing tests first)

 1. Spec: docs/specs/PHASE_INGEST_CSV_CHAT_SPEC.md (scope, I/O, drop rules, mapping tiers, failure modes, non-goals).
 2. Fixtures: tests/dataset_ingest/fixtures/ — corrupted CSV reproducing the real signatures (misaligned multi-line cells, ['TRUE','P4',...] reprs, dupes, empties) + clean
 issue_description/fix_provided CSV.
 3. Unit tests tests/dataset_ingest/test_csv_chat.py: heuristic maps issue_description/fix_provided; ambiguity → injected resolver called; resolver failure → MappingError; each drop rule;
 dedupe; threshold; chat output shape.
 4. Streaming tests (test_streaming.py): CSV yields messages records; replace test_csv_unknown_columns_become_text_record with mapping-required behavior; buffer flush order;
 EOF-before-buffer-full; drop counting.
 5. API integration tests: sync /file + /preview (monkeypatch resolver like test_ingest_qa.py mocks _invoke_skill); >50% drop → 400; Hermes-down → 400; async job path in
 test_ingest_large.py.
 6. Implement: csv_chat.py → skill file → converter.csv_to_chat → streaming iterator → ingest_jobs wiring → ingest_v2 wiring → frontend preview → delete dead code.
 7. Docs/DoD: README ingest section, ./release/ note, uv run ruff check --fix on changed files, uv run mypy apps packages, cd apps/web && npm run build.

 Edge cases

 Headerless CSV (Sniffer no-header → fail with message); single-column CSV → fail (no pair); Hermes returns identical/unknown columns → MappingError; ≤5-row files (flush at EOF); BOM/CRLF
 header normalization; dedupe keeps split determinism (_stable_fraction).

 Verification

 1. uv run pytest tests/dataset_ingest tests/api -q — all green.
 2. End-to-end: make dev, upload the corrupted fixture CSV via New Dataset → preview shows mapping + drop counts; save; inspect data/datasets/.../train.jsonl → every row is
 {"messages":[...]} with clean content.
 3. Re-ingest the original AX1 CSV, retrain a short run (~30 iters, LR 1e-5), then uv run python -m mlx_lm generate --model Qwen/Qwen3-1.7B --adapter-path runs/<id>/adapter --prompt "<an
 issue description>" → coherent fix text, and a plain chat question no longer collapses to <think>!.
 4. Negative: upload a garbage-majority CSV → 400 with drop-ratio error; stop Ollama, upload an ambiguous-schema CSV → 400 with "start Ollama or rename columns" error.