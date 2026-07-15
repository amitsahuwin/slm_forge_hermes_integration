# Plan: Dynamic model registry (Models tab)

Date: 2026-07-15 · Spec: `docs/specs/2026-07-15-models-registry.md` ·
ADR: `docs/adr/0008-dynamic-model-registry.md`

## Goal

Ship a self-service Models tab that registers HuggingFace models into a dynamic
catalog overlay, surfaces the registration as a Jobs-tab job, and makes
registered models appear everywhere with zero hardcoding and no regression.

## Approach

DB overlay on the seed catalog. Two tables keep concerns clean: a tenant-scoped
`ModelDownloadJob` (transient work, Jobs tab) and a global `RegisteredModel`
(durable catalog). All catalog lookups route through `effective_catalog()` =
seeds + registry, so every existing consumer becomes dynamic without touching
its call sites. "Download" = register + validate (HF Hub API); weights stay
fetched by the worker at train time → training path untouched.

## Build order (TDD, red → green)

1. **Models** — `ModelDownloadJob`, `RegisteredModel`; register both in
   `db.py` so `create_all` picks them up. Tests: table creation + round-trip.
2. **Dynamic catalog** — add `_registered_as_catalog_models()` +
   `effective_catalog()`; refactor `find_by_model_id`, `allowed_model_ids`,
   `get_model_by_key`, `default_model_id` to iterate the merged view; keep public
   signatures + seeds intact. Tests: merge, `validate_run_request` on registered
   id, legacy view skips cuda-only.
3. **Download service** — `model_download_jobs.py`: pure detection helpers
   (`infer_backend`, family/params/memory), `_fetch_model_meta` (HfApi + bounded
   retry), row transitions, `_upsert_registered_model`, `_schedule_download`.
   Tests (mock `HfApi`): success, missing, gated-without-token, override,
   reconciler.
4. **Router + aggregator + reconciler** — `POST /download`, `GET /registry`,
   `DELETE /registry/{key}` in `routers/models.py`; `modeldownload` kind +
   `_resolve_model_download` in `routers/jobs.py`; orphan reconciler in `db.py`.
   OPA `model` resource (admin create/delete; read open). Tests: 202/422/404,
   admin-only, cross-tenant 404, resolver shape.
5. **Frontend** — `pages/Models.tsx`; route in `App.tsx`; nav tab in `Nav.tsx`;
   `models` API client + types in `lib/api.ts`; `model` resource + `models` nav
   in `permissions.ts`; `modeldownload` in `Jobs.tsx`; Models entry in
   `Product.tsx`. Type gate: `npm run build`.
6. **Docs + verification** — spec, plan, ADR, README, release notes; full suite,
   ruff, mypy, OPA, npm build; `graphify update .`.

## Verification

- `uv run pytest -q` (full suite), feature subsets, jobs router.
- `uv run ruff check` on changed files; `uv run mypy apps packages` (only
  pre-existing findings remain).
- `make opa-test`.
- `cd apps/web && npm run build`.
- Manual: Models → paste `Qwen/Qwen2.5-1.5B-Instruct` → Download → `/jobs?id=
  modeldownload:<id>` polls to succeeded → appears in New Run dropdown.

## Non-breaking guarantees

- Public catalog signatures unchanged; seeds always present; training path
  untouched.
- New tables additive; new job kind additive to the aggregator.
- `_legacy_view` guarded against cuda-only entries.

## Status

Implemented. Full suite green (635 passed), frontend type gate green, OPA 33/33,
ruff clean on changed files.