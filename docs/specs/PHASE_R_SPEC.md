# Phase R Spec — Remote-worker hardening: atomic claiming, leases, dataset/artifact transfer

> **Status:** approved for implementation · **Date:** 2026-06-12
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` §4.4, §5
> **Depends on:** Phase O (`Run.trainer_backend`), Phase Q (CUDA worker)

---

## 1. Problem

Three gaps block a CUDA worker on a separate machine:

1. **Racy pickup.** Workers `GET /runs?status=queued` then `PATCH
   status=running` — two workers can grab the same run, and an MLX worker
   can grab a CUDA run.
2. **Recovery friendly-fire.** `_recover_stranded_runs_and_sessions()`
   re-queues **every** `running` run at API startup. With a remote worker
   mid-training, an API restart would double-schedule the run. (This
   already bites today: restarting the API container re-queues a run the
   host Mac trainer is still executing.)
3. **Shared-filesystem assumption.** The worker reads
   `data/datasets/<name>/` and writes `runs/<id>/adapter/` on the API
   host's disk. A remote box has neither.

## 2. Goals / non-goals

**Goals**

- G1. **Atomic, backend-aware claiming**: `POST /api/v1/runs/claim`
  `{backend, worker_id}` → oldest matching queued run, claimed with a
  compare-and-swap so two workers can never get the same run. Runs whose
  `trainer_backend` is NULL (pre-Phase-O rows) claim as `mlx`.
- G2. **Lease**: claims record `claimed_by` / `claimed_at`. A claim is
  *expired* when the run is `running` and its last activity (latest
  metric `recorded_at`, else `claimed_at`) is older than
  `SLM_FORGE_CLAIM_TIMEOUT_MIN` (default 60). Expired claims are released
  (re-queued, with an explanatory `error_message`) lazily on every claim
  attempt and at API startup.
- G3. **Lease-aware startup recovery**: only legacy rows
  (`claimed_at IS NULL`) and expired claims are re-queued; an actively
  reporting remote run survives an API restart.
- G4. **Dataset download**: `GET /api/v1/datasets/{name}/archive` →
  tar.gz of the dataset dir (name strictly validated — no traversal).
- G5. **Adapter upload**: `POST /api/v1/runs/{run_id}/artifacts`
  (multipart tar.gz) → safely extracted under `runs/<id>/` (member paths
  validated: relative, no `..`, no symlinks/absolute paths).
- G6. **Worker remote mode** (`SLM_FORGE_REMOTE_WORKER=true`):
  the trainer downloads the dataset archive when it's missing locally and
  uploads the adapter after a successful run. Default `false` keeps the
  shared-filesystem fast path byte-identical.
- G7. **Backend filter** on `GET /runs` (`?backend=`), additive.

**Non-goals:** UI surfacing of workers/claims (Phase S), heartbeat schema
changes (folded into Phase S), claim renewal API (the metric stream *is*
the renewal), export-artifact download for remote exporters (exporter
stays co-located with the API host for now).

## 3. Interfaces

### 3.1 `Run` model + migrations (`apps/api/models/run.py`, `services/db.py`)

```python
claimed_by: str | None = None      # "hostname:pid" of the claiming worker
claimed_at: datetime | None = None
```

`_RUN_MIGRATIONS` += `("claimed_by", "TEXT")`, `("claimed_at", "TIMESTAMP")`.

### 3.2 Claim service (`apps/api/services/claims.py`, new)

```python
CLAIM_TIMEOUT_ENV = "SLM_FORGE_CLAIM_TIMEOUT_MIN"   # default "60"

def claim_timeout() -> timedelta
def last_activity(db, run) -> datetime | None       # max(metric ts, claimed_at)
def release_expired_claims(db) -> int               # returns #released
def claim_next_run(db, backend: str, worker_id: str) -> Run | None
```

`claim_next_run`:
1. `release_expired_claims(db)`.
2. Candidates: `status == queued AND (trainer_backend == backend OR
   (backend == "mlx" AND trainer_backend IS NULL))`, oldest `created_at`
   first.
3. For each candidate, CAS via
   `UPDATE runs SET status='running', claimed_by=:w, claimed_at=:now,
   started_at=COALESCE(started_at, :now) WHERE id=:id AND
   status='queued'`; rowcount 1 → claimed; else next candidate.

`release_expired_claims`: for `running` rows with `claimed_at IS NOT
NULL` and `last_activity < now - timeout` → `status=queued`,
`claimed_by=claimed_at=NULL`, `error_message` noting the lease expiry.

### 3.3 Router (`apps/api/routers/runs.py`)

```python
class RunClaim(BaseModel):
    backend: str = "mlx"
    worker_id: str

POST /runs/claim  → Run | None      # None (JSON null) = queue empty
GET  /runs?backend=cuda             # additive filter
```

Route ordering: `/claim` registered **before** `/{run_id}` (FastAPI
matches in declaration order; `claim` would otherwise parse as a run id).

### 3.4 Transfer endpoints

- `GET /api/v1/datasets/{name}/archive` (`routers/datasets.py`):
  `name` must match `^[A-Za-z0-9._-]+$` (422 otherwise), dir must exist
  (404). Returns `application/gzip` bytes of a tar.gz whose members are
  `<name>/train.jsonl`, etc.
- `POST /api/v1/runs/{run_id}/artifacts` (`routers/runs.py`):
  multipart field `archive`; run must exist (404). Extraction root:
  `ARTIFACTS_ROOT / str(run_id)` (module constant `/app/runs`,
  monkeypatchable). Member validation: reject absolute paths, `..`
  segments, links. Response `{"files": n, "adapter_path": "<root>/adapter"}`.
  On success also PATCHes nothing — the worker still owns the final Run
  PATCH (keeps one writer for run state).

### 3.5 Worker (`packages/trainer/`)

- `transfer.py` (new):
  - `ensure_dataset_local(dataset, api_url) -> Path` — returns
    `DATA_ROOT/dataset`, downloading+extracting the archive when
    `train.jsonl` is missing. Safe extraction mirrors the server rules.
  - `upload_adapter(run_id, adapter_dir, api_url) -> bool` — tars
    `adapter/` (arcname-rooted) and POSTs it; never raises (returns False
    on failure so the run still completes with a warning).
- `__main__.py`: pickup becomes
  `POST /runs/claim {backend: backend.name, worker_id: f"{hostname}:{pid}"}`;
  the old GET+PATCH(running) pair is gone (the claim *is* the running
  transition — `runner.py` keeps its `_patch_run(status="running")`,
  which is now an idempotent no-op state-wise but still updates UI SSE).
- `runner.py`: dataset guard calls `ensure_dataset_local` when
  `SLM_FORGE_REMOTE_WORKER` is truthy; after a successful run in remote
  mode, `upload_adapter` runs before the final PATCH.

### 3.6 Startup recovery (`apps/api/main.py`)

Replaces the unconditional re-queue of `running` runs:
- `claimed_at IS NULL` (legacy/local rows) → re-queue (old behavior).
- claimed + expired (via `release_expired_claims`) → re-queue.
- claimed + alive → left running.
Sessions logic unchanged.

## 4. Acceptance criteria

- A1. Two sequential claims return two different runs; claim on an empty
  queue returns `None`; the claimed run is `running` with
  `claimed_by`/`claimed_at`/`started_at` set.
- A2. Backend isolation: an `mlx` claim never returns a `cuda` run and
  vice versa; NULL-backend rows claim as `mlx`.
- A3. CAS: a candidate flipped to `running` between SELECT and UPDATE is
  skipped, not double-claimed (simulated in test).
- A4. Lease: a `running` run with stale `claimed_at` and no metrics is
  released on the next claim; one with a *recent metric* is not, even if
  `claimed_at` is old.
- A5. Startup recovery preserves an actively-reporting claimed run,
  re-queues legacy `claimed_at IS NULL` rows.
- A6. Archive endpoint: tar.gz round-trips the dataset files; bad names
  (traversal attempts) rejected; unknown dataset 404.
- A7. Upload endpoint: valid adapter tar extracts to
  `<root>/<run_id>/adapter/...`; archives containing `../` or absolute
  members are rejected wholesale (400) with nothing written.
- A8. Worker transfer helpers: missing dataset triggers download +
  extract (faked httpx); present dataset short-circuits with no HTTP
  call; `upload_adapter` sends multipart and survives API errors.
- A9. Full suite green (86 prior + new); ruff clean on touched files.

## 5. Test plan

```
tests/api/test_run_claiming.py        # A1–A5 (claims service + endpoint fn)
tests/api/test_transfer_endpoints.py  # A6–A7
tests/trainer/test_transfer.py        # A8
```

Hermetic: temp SQLite, temp dirs, monkeypatched module roots and httpx.

## 6. Rollout

- **Local Mac setup: nothing changes.** Remote mode is opt-in per worker;
  the claim endpoint replaces the worker's internal pickup but the
  external behavior (oldest queued run trains next) is identical.
- Lease default 60 min — generous vs the 10-step metric cadence; tighten
  via `SLM_FORGE_CLAIM_TIMEOUT_MIN` once remote runs are routine.
- Workers and API must deploy together this phase (the worker now
  requires `/runs/claim`).
- `.env.example` documents `SLM_FORGE_REMOTE_WORKER` and
  `SLM_FORGE_CLAIM_TIMEOUT_MIN`; the Dockerfile.trainer-cuda header drops
  the shared-volume caveat for `data/` (volumes become optional caching).
