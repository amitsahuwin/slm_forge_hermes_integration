# Plan — Large Dataset Upload (background ingest jobs)

**Date:** 2026-07-14
**Spec:** `docs/specs/2026-07-14-large-dataset-upload.md`
**Approach:** A — in-process asyncio task, DB-backed `IngestJob`, stream to object store.

TDD throughout: red → green → refactor. Run `uv run pytest -q` after each step;
`ruff`/`mypy` on changed files; `apps/web` build for the type gate.

## Step 0 — Config

- Add `SLM_FORGE_MAX_UPLOAD_BYTES` (default 500 MB) and
  `SLM_FORGE_INGEST_SYNC_MAX_BYTES` (default 10 MB) reads with startup
  validation (fail fast if unparseable / ≤ 0).
- Document both in `.env.example`.

## Step 1 — `IngestJob` model  (test-first)

- `tests/api/test_ingest_job_model.py`: construct/persist a row; enum values;
  defaults; `tenant_id` indexed.
- `apps/api/models/ingest_job.py`: `IngestStatus` enum + `IngestJob` SQLModel.
- Register in `apps/api/services/db.py::init_db` (import for `create_all`).

## Step 2 — Streaming primitives  (test-first, pure functions — heaviest unit coverage)

- `tests/dataset_ingest/test_streaming.py`:
  - `iter_jsonl_records`: records split across chunk boundaries; blank lines;
    bad-JSON lines counted as dropped, not raised.
  - `iter_csv_records`: header→dict; quoted fields; chunk boundaries.
  - `StreamingSplitWriter`: guarantees `valid≥4`/`canary≥1` for N≥5;
    deterministic for a fixed input; ratio distribution for large N;
    `records_total < 5` signalled to caller.
- `packages/dataset_ingest/streaming.py`:
  - `async def iter_jsonl_records(chunks: AsyncIterable[bytes]) -> AsyncIterator[tuple[dict|None, bool]]`
    (or yields records + a running dropped counter — final API TBD in impl).
  - `iter_csv_records(...)` mirror.
  - `StreamingSplitWriter(dataset_dir)` with `.write(record)` and `.finalize()
    -> counts`, applying the seeded-minimums + hash-ratio rule from the spec.
    Reuses `target_min_valid=4`, `target_min_canary=1`, ratios `(.8,.15,.05)`.

## Step 3 — Job runner service  (test-first)

- `tests/api/test_ingest_jobs_service.py` (uses `LocalObjectStore`, real DB, no
  mocks): seed a raw object + `queued` row → run `_run_ingest_job` →
  assert dataset files written, counts, status `succeeded`, raw deleted.
  Failure paths: too-few-records, >50% bad lines → `failed`, no dataset.
- Implement `_run_ingest_job` (in `apps/api/services/ingest_jobs.py`):
  load row → `processing` → `store.get(raw_key)` → parse → `StreamingSplitWriter`
  → README + tallies → `succeeded` + delete raw; on exception → `failed`.
  Offload blocking parse/write via `asyncio.to_thread` in bounded batches.

## Step 4 — Upload endpoint  (test-first)

- `tests/api/test_ingest_large.py`:
  - 202 + `ingest:N` happy path (await task, assert dataset).
  - over-cap → 413, no row, no object.
  - duplicate name → 409.
  - non-jsonl/csv large → 422.
  - streamed to store without buffering (assert object exists; monkeypatch to
    assert chunked `put`).
- `POST /api/v1/ingest/file/large` in `apps/api/routers/ingest_v2.py`:
  validate name/format/size; stream `file` → `store.put` with an async chunk
  generator enforcing the byte cap; insert row; `asyncio.create_task`; return 202.

## Step 5 — Jobs aggregator `ingest` kind  (test-first)

- Extend `tests/api/test_jobs_router.py`: `ingest:N` resolves; progress/links;
  cross-tenant → 404; unknown id → 404.
- `jobs.py`: add kind + `_resolve_ingest` via `scope_query`.

## Step 6 — Restart reconciler  (test-first)

- Test: insert `processing` + `queued` rows → `init_db()` (or the reconciler
  fn) → both `failed` with the restart message.
- Implement `_reconcile_orphaned_ingest_jobs()` called from `init_db()`.

## Step 7 — Frontend

- `apps/web/src/pages/NewDatasetV2.tsx`: route file create by `file.size`;
  for large files call the async endpoint with upload progress, disable
  `force_ollama` (+ note "pre-formatted JSONL/CSV only"), navigate to
  `/jobs?id=ingest:<id>`; update "10 MB max" copy → "500 MB max".
- `apps/web/src/pages/Jobs.tsx`: add `ingest` to the kind union + `KIND_HINTS`,
  auto-poll every ~2 s while `queued|processing`, add an example id.
- `cd apps/web && npm run build` (tsc gate).

## Step 8 — Verify & document

- Full suite green; coverage ≥ 90% on new modules; `ruff`/`mypy` clean.
- Manually drive the UI (upload > 10 MB → Jobs tab → dataset) or provide
  `curl` steps if the browser isn't available.
- Update `README.md` (upload limits + Jobs tab), `.env.example`, and
  `./release/` notes. Commit via `commit_message.md`.

## Red-team notes (resolved)

- **RAM blowup** → streamed put/get + `StreamingSplitWriter`; never hold the
  whole file or record list.
- **Lying `Content-Length`** → enforce byte cap mid-stream, not just on header.
- **Orphaned jobs on restart** → DB-backed state + startup reconciler.
- **Partial dataset on failure** → splits are written to a staging dir
  `.ingest-<id>.tmp/` and **atomically renamed** to `<name>/` only on success;
  failure removes staging, so a partial dataset is never listed. Raw blob kept
  on failure for debugging.
- **Cross-tenant leakage** → `tenant_key` + `scope_query`, 404 not 403.
- **Split guarantees on odd inputs** → seeded-minimums rule + `N<5` precondition.
