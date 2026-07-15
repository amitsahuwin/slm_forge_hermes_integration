# Spec: Dynamic model registry (Models tab)

Date: 2026-07-15 · Status: implemented · Related: `docs/adr/0008-dynamic-model-registry.md`,
`docs/plans/2026-07-15-models-registry.md`

## Problem

The model catalog is a hardcoded Python list (`CATALOG_V2` in
`apps/api/services/model_catalog.py`). Using a model that isn't in that list
requires editing source and redeploying. Users need a self-service **Models**
tab: browse the available catalog and add a new model by pasting a HuggingFace
repo id (e.g. `meta-llama/Llama-3.2-1B-Instruct`, `Qwen/Qwen3-1.7B`,
`google/gemma-3-1b-it`). The add must surface as a Jobs-tab job, and once done
the model must appear **dynamically** in New Run / New Experiment with no
hardcoding and no regression.

## Scope

- Browse the effective catalog (built-in seeds + registered models).
- Register a model from a HF repo id: **register + validate** semantics — the
  job validates the repo via the HF Hub API and persists a global catalog entry.
  Weights stay fetched by the trainer worker at train time, so the training path
  is untouched.
- The registration runs as a background job, surfaced in the Jobs tab as
  `modeldownload:<id>`.
- Registered models appear everywhere the catalog is read (dropdowns, validation).
- Admins can remove a registered model.

### Non-goals

- Physically pre-downloading weights into object storage or changing how the
  trainer resolves the model at train time.
- Per-tenant registry isolation — the registry is **global** (like the seeds).
  The download *job* stays tenant/user-scoped.
- Editing built-in seed entries.

## Users & flow

1. User opens **Models**, sees the merged catalog (each row tagged `built-in`
   or `downloaded`, with per-backend variant, status, gated flag, memory hint).
2. User pastes an HF id, optionally overrides the backend (Auto-detect / MLX /
   CUDA), submits. Frontend `POST /api/v1/models/download` → `202` →
   navigates to `/jobs?id=modeldownload:<id>`.
3. Jobs tab silently polls until the job reaches a terminal state. On success
   the model is in the catalog; New Run / New Experiment list it automatically.
4. Admin may remove a registered model via the Remove button (`DELETE`).

## Data model

Two SQLModel tables, auto-created by `create_all` (registered in
`apps/api/services/db.py`).

### `model_download_jobs` — `ModelDownloadJob` (tenant-scoped, transient work)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `tenant_id` | str (index) | captured from request Identity |
| `user_id` | str | |
| `hf_id` | str | requested repo id |
| `target_backend` | str | `mlx` \| `cuda` (override or auto-detected) |
| `status` | enum | `queued`→`processing`→`succeeded`\|`failed` |
| `registered_key` | str? | catalog key produced on success |
| `detected_family` / `detected_params` / `detected_arch` | str? | from HF meta |
| `gated` | bool | |
| `error_message` | str? | surfaced in Jobs tab |
| `created_at` / `started_at` / `completed_at` | datetime | |

### `registered_models` — `RegisteredModel` (global, durable catalog overlay)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `key` | str (unique index) | logical catalog key, slug of repo name |
| `label` | str | |
| `family` / `size_params` / `recommended_method` | str | `recommended_method` default `lora` |
| `backend` | str | single variant: `mlx` \| `cuda` |
| `model_id` | str (unique index) | HF repo id |
| `min_memory_gb` | float | coarse heuristic hint |
| `quant` | str? | `nf4` for cuda, none for mlx |
| `status` | str | `untested` on register |
| `gated` | bool | |
| `notes` | str | |
| `created_by_user_id` / `created_by_tenant_id` | str | provenance only |
| `created_at` | datetime | |

Each `RegisteredModel` maps 1:1 to a `CatalogModel` with one `BackendVariant`,
merged into `effective_catalog()`.

## Interfaces

- `GET /api/v1/models/v2` → `list[CatalogModel]` — merged `effective_catalog()`
  (seeds + registry). Consumed by the UI and both creation dropdowns. **dynamic**
- `GET /api/v1/models` → legacy flat shape, derived from each model's mlx
  variant; cuda-only registered rows are skipped (guarded `.get("mlx")`).
- `GET /api/v1/models/registry` → `list[RegistryEntry]` — registered rows only,
  for the manage/delete list. Readable by any authenticated user.
- `POST /api/v1/models/download` → `202 {job_id:"modeldownload:<id>", hf_id,
  target_backend, status}`. Admin-only (`@requires("create","model")`). Validates
  `hf_id` against `HF_ID_RE` (422) and backend ∈ {mlx,cuda} (422).
- `DELETE /api/v1/models/registry/{key}` → `204`. Admin-only
  (`@requires("delete","model")`); `404` if key absent.
- Jobs resolver `modeldownload:<id>` (`_resolve_model_download`) — tenant-scoped
  via `scope_query` (cross-tenant → 404); progress surfaces
  `{hf_id, backend, family, params, arch, gated, registered_key}`; deep link
  `/runs/new` once succeeded else `/models`.

## Processing worker

`apps/api/services/model_download_jobs.py` drives `queued → processing →
succeeded|failed`, never raises:

- `_fetch_model_meta` — `HfApi().model_info(hf_id, token=HF_TOKEN)`. Terminal:
  `RepositoryNotFoundError`, `GatedRepoError`, or gated-without-token → `failed`
  with a clear message (no silent fallback). Transient (`HfHubHTTPError`,`OSError`)
  → retry ≤3 with exponential backoff + jitter.
- Detection: `gated`, params (`safetensors.total`), arch (`config.architectures`),
  family (name/arch heuristic), backend (`infer_backend`: `mlx-community/*` /
  `*mlx*` / `*-4bit|-8bit` → mlx, else cuda; user override honored).
- On success: upsert `RegisteredModel` unique by `model_id` (update in place if
  the repo was registered before), set `registered_key`.
- Restart reconciler in `db.py` drives orphaned `queued`/`processing` rows →
  `failed` at startup.

## Config

- `HF_TOKEN` (existing, `.env`) — used only to read gated/private repos; never
  logged.
- No new config keys. `SLM_FORGE_ENFORCE_CATALOG` still gates run validation and
  now covers registered models automatically (they flow through
  `find_by_model_id`).

## Security & reliability (AAA + OWASP)

- **AuthN/AuthZ**: listing open to authenticated users; mutations
  (`POST /download`, `DELETE /registry/{key}`) admin-only via `@requires` +
  OPA `model` resource in `policies/role_matrix.rego`.
- **Accounting**: register/delete logged with tenant, user, hf_id/key — never
  secrets.
- **Input validation**: `HF_ID_RE` (`^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$`)
  before any network call; backend enum-checked. Never shells out — HfApi only.
- **Isolation**: download job tenant-scoped via `scope_query`; registry
  intentionally global.
- **Resilience**: bounded retry with backoff+jitter; runner never raises;
  reconciler recovers orphans.

## Acceptance criteria

- Registering a valid repo yields `202` `modeldownload:<id>`, polls to
  `succeeded`, and the model appears in `GET /v2` + both dropdowns.
- Missing / gated-without-token repo → job `failed` with a clear Jobs-tab message.
- `validate_run_request` accepts a registered `model_id`; still 422 on
  broken/mismatched/unknown.
- Invalid `hf_id` → 422; non-admin mutation denied; cross-tenant job lookup → 404.
- Full backend suite green; frontend type gate green; OPA tests green.

## Future

- Optional per-tenant registries.
- Pre-warming weights into object storage.
- Editable seed metadata / promoting a registered model to `stable` after a
  successful run.