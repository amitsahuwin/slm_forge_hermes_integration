# ADR-0008 — Dynamic model registry (DB overlay on seed catalog)

- **Status:** Accepted
- **Date:** 2026-07-15
- **Related:** `docs/specs/2026-07-15-models-registry.md`,
  `docs/plans/2026-07-15-models-registry.md`, ADR-0007 (per-user isolation),
  CLAUDE.md §15 (no versioned modules), §35 (tenant isolation)

## Context

The model catalog was a hardcoded Python list (`CATALOG_V2` in
`apps/api/services/model_catalog.py`). Adding a model meant editing source and
redeploying. Users need to self-register HuggingFace models from the UI, have
the operation appear in the Jobs tab, and have the model show up dynamically in
New Run / New Experiment — with no regression to the existing training path,
which relies on the trainer worker fetching weights from HF at train time (the
API is Dockerized and shares no filesystem with the host workers).

Three decisions were required:

1. **Where does the dynamic catalog live**, and how do all existing consumers
   become dynamic without touching each call site?
2. **What does "download" mean** given API/worker filesystem separation?
3. **What is the registry's visibility model** and how is mutation authorized?

## Decision

### 1. DB overlay merged through `effective_catalog()`

A new global `RegisteredModel` table overlays the seed list. All catalog reads
route through `effective_catalog()` = `CATALOG_V2` (seeds) + registry rows merged
by key. `find_by_model_id`, `allowed_model_ids`, `get_model_by_key`, and
`default_model_id` iterate the merged view, so `validate_run_request` and both
dropdowns become dynamic automatically. Public signatures and seeds are
unchanged — no `*_v2` module (CLAUDE.md §15), change in place.

### 2. "Download" = register + validate

The job validates the repo via `HfApi().model_info` and persists a catalog
entry; it does **not** fetch weights. Weights continue to be fetched by the
trainer worker at train time. This keeps the training path untouched and the
change strictly additive/non-breaking.

### 3. Global registry, admin-only mutation; tenant-scoped job

The registry is global (like the seeds) so a registered model is usable
everywhere. The registration **job** (`ModelDownloadJob`) is tenant/user-scoped
and surfaced in the Jobs tab as `modeldownload:<id>` via the federated resolver.
Mutations (`POST /download`, `DELETE /registry/{key}`) are admin-only through the
`@requires` decorator + a new OPA `model` resource; listing is open to
authenticated users.

## Rejected alternatives

- **Per-tenant registry** — rejected as speculative (YAGNI, CLAUDE.md §35); the
  seeds are global too, and no requirement exists for per-tenant model isolation.
  Data isolation is preserved where it matters: the download *job* is scoped.
- **Pre-download weights into object storage** — rejected: it would change the
  trainer's model resolution and risk the existing training path for no
  near-term benefit. Register+validate is sufficient.
- **Mutate `CATALOG_V2` in memory / write back to source** — rejected: not
  durable, not multi-instance safe, and violates "data in a DB, not disk."

## Consequences

- Registered models appear everywhere with no per-consumer changes; the seed
  catalog remains the source of defaults.
- Two additive tables; one additive job kind; `_legacy_view` guards cuda-only
  rows (`.get("mlx")`) so cuda-only registrations don't break the legacy shape.
- HF availability is a runtime dependency of *registration* only (bounded retry
  + clear terminal failures); training already depended on HF at train time.

## Verification

- `uv run pytest -q` full suite green (635 passed); feature + jobs subsets green.
- `make opa-test` green (33/33) incl. the new `model` resource matrix.
- `cd apps/web && npm run build` type gate green.
- Manual: register `Qwen/Qwen2.5-1.5B-Instruct` → Jobs polls to succeeded →
  appears in New Run dropdown → run trains via the unchanged worker path.