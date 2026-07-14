# Spec — Large Dataset Upload (background ingest jobs)

**Date:** 2026-07-14
**Status:** Approved (design), implementation in progress
**Owner:** amit

## Problem

Dataset file upload is capped at **10 MB** (`apps/api/routers/ingest_v2.py:41`,
`MAX_BYTES`). The endpoint reads the whole file into RAM (`await file.read()`)
and processes it *synchronously* inside the request (detect → parse → optional
Ollama convert → split → write). Both the cap and the synchronous-in-request
design prevent uploading large corpora. Users need to upload files far larger
than 10 MB (hundreds of MB) and track progress via a **Job ID in the Jobs tab**.

## Scope

Add a **background, streaming** upload path for large files while leaving the
existing small-file synchronous path unchanged.

- **Max size:** configurable, default **500 MB** (`SLM_FORGE_MAX_UPLOAD_BYTES`).
- **Big files are pre-formatted only:** streamable **JSONL** and **CSV**;
  parsed record-by-record at constant RAM. **No Ollama** conversion on the
  large path (infeasible at this size).
- **Durable job state:** a new DB-backed `IngestJob` table (not the in-memory
  registry used by synth/research), so a job survives an API restart and is
  tenant-scoped like `Run`/`Export`.
- **Jobs tab integration:** a new `ingest` kind in the composite-id aggregator
  (`apps/api/routers/jobs.py`), so `ingest:<id>` resolves in the existing Jobs
  page.

### Non-goals

- Presigned direct-to-object-store multipart upload (deferred; only needed for
  tens-of-GB scale — see "Future" below).
- A separate claimed ingest **worker** process (Approach B). The `IngestJob`
  schema is shaped so this is a purely additive future upgrade.
- Ollama auto-conversion of large files.
- Streaming of non-line-oriented formats (JSON array, markdown, arbitrary
  text) on the large path — rejected with an actionable message.
- Migrating existing on-disk datasets to be object-store-native (a broader,
  pre-existing effort).

## Users & flow

1. In **New Dataset → File**, the frontend routes by `file.size`:
   - `≤ 10 MB` → existing synchronous `POST /api/v1/ingest/file` (instant,
     Ollama available). **Unchanged.**
   - `> 10 MB` → new `POST /api/v1/ingest/file/large`. The browser streams the
     file (shows upload progress); on `202` it navigates to
     `/jobs?id=ingest:<id>`.
2. The Jobs page polls `GET /api/v1/jobs/ingest:<id>` every ~2 s while the
   status is `queued|processing`, then links to the finished dataset.

## Data model — `IngestJob`

New SQLModel table `ingest_jobs` (`apps/api/models/ingest_job.py`). Created by
`SQLModel.metadata.create_all` (no column migration — the model is registered
in `init_db()`).

| field | type | notes |
|---|---|---|
| `id` | int PK | composite id is `ingest:<id>` |
| `tenant_id` | str, indexed | isolation via `scope_query` |
| `user_id` | str | owner |
| `dataset_name` | str | validated (`^[a-z0-9][a-z0-9-_]*$`) |
| `status` | str enum | `queued` → `processing` → `succeeded` \| `failed` |
| `source_filename` | str \| None | provenance |
| `detected_format` | str \| None | `jsonl_*` \| `csv` |
| `raw_key` | str \| None | object-store key of the uploaded blob |
| `raw_bytes` | int | bytes streamed to store |
| `records_total` | int | valid records parsed |
| `train_count` / `valid_count` / `canary_count` | int | final split tallies |
| `dropped_count` | int | unparseable / invalid lines skipped |
| `error_message` | str \| None | failure surface |
| `created_at` / `started_at` / `completed_at` | datetime \| None | timing |

`IngestStatus` = `Enum("queued","processing","succeeded","failed")`.

## Interfaces

### `POST /api/v1/ingest/file/large`  (auth: `@requires("create","dataset")`)

Multipart form:
- `name: str` (required) — dataset name, validated; `409` if dataset exists.
- `file: UploadFile` (required) — streamed in fixed chunks to the object store.
- `description: str | None`.

Behaviour:
- Reject early with `413` when `Content-Length` exceeds the cap; **also** enforce
  the running byte total during streaming (the header can lie) and abort +
  delete the partial object + `413`.
- Stream the request body into the object store at
  `tenant_key(identity, kind="data", artifact_id="upload-<uuid>", filename=<safe>)`
  via `store.put(key, <async chunk iterator>, content_type=...)` — **constant
  API RAM**.
- Insert `IngestJob(status="queued", raw_key, raw_bytes, ...)`, commit, launch
  the asyncio processing task, and return:

```
202 { "job_id": "ingest:<id>", "dataset_name": "<name>", "status": "queued" }
```

- `422` if the detected format (from filename + head bytes) is not a streamable
  `jsonl_*`/`csv`; message directs the user to the standard uploader for other
  formats.

### Jobs aggregator — new `ingest` kind (`apps/api/routers/jobs.py`)

- Add `"ingest"` to `JobKind` / `_VALID_KINDS`.
- `_resolve_ingest(rid, identity, db)` → `scope_query(select(IngestJob), ...)`;
  cross-tenant miss returns **404** (consistent with other kinds).
- `JobDetail.progress = {raw_bytes, records_total, train, valid, canary,
  dropped, format}`; `links = {"detail": "/datasets/<name>"}` (populated once
  `succeeded`), `{"datasets": "/datasets"}` otherwise.

## Processing worker (in-process asyncio task)

`packages/dataset_ingest/streaming.py` (new) provides constant-RAM primitives;
`apps/api/routers/ingest_v2.py` (or a small `ingest_jobs.py` service) hosts the
task runner.

`_run_ingest_job(job_id)`:
1. Load row, set `status="processing"`, `started_at`.
2. Stream the raw object back (`store.get(raw_key)` → `AsyncIterator[bytes]`).
3. **Stream-parse** into records (blocking JSON/CSV work offloaded via
   `asyncio.to_thread` in bounded batches):
   - JSONL: split on newlines across chunk boundaries; `json.loads` each line;
     validate; skip + count bad lines (`dropped_count`).
   - CSV: header row → dict rows via a streaming `csv` reader.
4. **Deterministic streaming split** (`StreamingSplitWriter`): each record is
   assigned once and written immediately to `train/valid/canary.jsonl` inside a
   **staging dir** `user_datasets_dir(identity)/.ingest-<id>.tmp/` (constant
   RAM) — never directly into the final `<name>/`, so a partial/failed job is
   never visible in the datasets listing. Assignment rule, applied in stream
   order:
   - if `canary_count < target_min_canary (1)` → canary
   - elif `valid_count < target_min_valid (4)` → valid
   - else assign by a stable per-record hash to the `0.80/0.15/0.05` ratios.
   This **guarantees** `valid ≥ 4`, `canary ≥ 1` for any file with `≥ 5`
   records and is deterministic/reproducible for a given input.
5. Write `README.md` (counts + provenance) into staging, then **atomically
   rename** the staging dir to the final `<name>/` (fail `409` if `<name>/`
   appeared meanwhile). Update tallies, set `status="succeeded"`,
   `completed_at`; **delete the raw upload object**.
6. On error: remove the staging dir (no partial dataset), set
   `status="failed"`, `error_message`; **keep** the raw object for debugging.

**Preconditions enforced by the job (fail with guidance):**
- `records_total < 5` → too few records to form valid/canary splits; use the
  standard uploader.
- unparseable ratio `> 50%` → likely wrong/corrupt format.

## Config

- `SLM_FORGE_MAX_UPLOAD_BYTES` — int bytes, default `524288000` (500 MB).
  Validated at startup (fail fast if unparseable or `<= 0`).
- Existing 10 MB `MAX_BYTES` becomes the **sync-path threshold** and the
  frontend's routing boundary (`SLM_FORGE_INGEST_SYNC_MAX_BYTES`, default
  `10485760`).

## Security / reliability (AAA + OWASP)

- **AuthZ:** `@requires("create","dataset")` on upload; `read` on the aggregator.
- **Tenant isolation:** raw blob keyed under the tenant prefix via `tenant_key`;
  `IngestJob` filtered through `scope_query`; cross-tenant lookups → 404.
- **Input validation:** dataset name regex; key validation (`validate_key`
  rejects traversal/backslash/null); byte cap enforced pre- and mid-stream.
- **No silent fallback:** unsupported formats and too-small files fail loudly
  with actionable messages; parse errors are counted and surfaced, not hidden.
- **Restart recovery:** `init_db()` runs a reconciler that flips any
  `processing`/`queued` ingest jobs (orphaned by a crash/restart) to `failed`
  with `error_message="interrupted by API restart"`. Deterministic startup.
- **Logging:** structured, keyed by `job_id` + `tenant_id`; never log payload
  contents or secrets.

## Acceptance criteria

1. A 50 MB JSONL upload returns `202 {job_id:"ingest:N"}`; polling the Jobs tab
   shows `processing` then `succeeded`; the dataset appears with correct
   train/valid/canary counts and `valid ≥ 4`, `canary ≥ 1`.
2. A file over `SLM_FORGE_MAX_UPLOAD_BYTES` is rejected `413` and leaves no
   dataset and no orphaned object.
3. Uploading with an existing dataset name → `409`.
4. A large non-JSONL/CSV file (e.g. markdown) → `422` with guidance.
5. Cross-tenant `GET /jobs/ingest:<id>` → `404`.
6. A malformed-line-heavy file (>50% bad) → job `failed` with a clear message;
   partial dataset is not published.
7. Simulated restart mid-job → reconciler marks it `failed`.
8. Peak API RSS during a 500 MB upload stays bounded (streamed, not buffered).
9. Small files (`≤ 10 MB`) still use the synchronous path unchanged.
10. `uv run pytest -q` green; new code coverage ≥ 90%; `ruff`/`mypy` clean on
    changed files; `apps/web` `tsc` gate passes.

## Future (out of scope, additive)

- Presigned multipart direct-to-Ozone for tens-of-GB uploads.
- Promote the asyncio task to a **claimed ingest worker** (reuse the trainer
  claim-queue pattern) — no schema change required.
- `ijson`-based streaming for large JSON arrays.
- Cancel endpoint (`status="cancelled"`).
