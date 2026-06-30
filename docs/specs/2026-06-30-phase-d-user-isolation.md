# Phase D Spec — Per-user tenant isolation (clean-slate cutover)

> **Status:** approved · **Date:** 2026-06-30 · **Owner:** Amit
> **Plan:** `docs/plans/2026-06-30-phase-d-user-isolation.md`
> **ADR:** `docs/adr/0007-per-user-isolation-cutover.md`
> **Release:** `release/0.9.0.md`
> **Predecessors:** Phase C (multi-tenancy foundation) — `docs/specs/2026-06-29-multi-tenancy-identity.md`, ADR-0006.

---

## 1. Problem

Phase C added the columns and helpers for tenant isolation but did not wire them into every router. Concretely:

- `apps/api/routers/runs.py`, `sessions.py`, `exports.py`, and the metrics sub-endpoint on `runs.py` issue **unscoped** `select(Model)` / `db.get(Model, id)` calls. A user in tenant `acme` can list, fetch, patch, delete, and download artifacts created by a user in tenant `local`.
- `apps/api/models/hermes_trace.py` carries `tenant_id` but **no** `user_id` — every authenticated user in a tenant sees every trace in that tenant, contradicting the per-user model that runs/sessions follow.
- Datasets live as plain files under `data/datasets/` with no per-user or per-tenant separation. A user can list any other user's uploaded dataset.
- `create_run`, `create_session`, `create_export` and ingest endpoints do **not** stamp `tenant_id`/`user_id`, so even after Phase C every new row is born unscoped (`NULL`), worsening the boundary blur.

CLAUDE.md §35: "Data isolation is non-negotiable — no cross-tenant access." Phase C delivered the schema; Phase D delivers the enforcement and removes pre-Phase-D rows from the picture via a clean-slate cutover (user's explicit choice — no migration).

---

## 2. Requirements

### R1 — Boundary semantics

Every read/list/get/patch/delete endpoint that returns user-owned data MUST apply the following predicate before returning rows:

```
WHERE tenant_id = Identity.tenant_id
  AND (Identity.is_admin OR user_id = Identity.user_id)
  AND (NOT Identity.is_worker)            -- workers cannot enumerate
```

`apps/api/services/scoping.py:26` `scope_query()` already implements this exactly. Phase D uses it verbatim; no new helper.

Every write endpoint MUST stamp the new row's `tenant_id` and `user_id` from `current_identity(request)` and MUST ignore any client-supplied values for those fields.

### R2 — Models touched

| Model | File | Column status today | Phase D change |
|---|---|---|---|
| `Run` | `apps/api/models/run.py` | `tenant_id`, `user_id`, `role` nullable | Stamp on create; scope on read; reject mutation in `RunPatch` |
| `TrainingSession` | `apps/api/models/session.py` | same | same |
| `Export` | `apps/api/models/export.py` | same | same |
| `Metric` | `apps/api/models/metric.py` | same | derive from parent `Run` on write |
| `HermesTrace` | `apps/api/models/hermes_trace.py` | `tenant_id` only | **Add `user_id`** (indexed); stamp from `Identity` for API-side writes, from parent `Run` for worker-side writes; scope on read |
| `ChatConversation` / `ChatMessage` | `apps/api/models/chat.py` | already scoped (Phase C) | Verify; no expected change |
| `AutoFixAttempt` | `apps/api/models/autofix.py` | `tenant_id`+`user_id` exist | Stamp + scope its read endpoints |
| `Dataset` (file-only, no model) | n/a | n/a | New filesystem layout: `data/datasets/global/<name>/` (bundled, read-only) and `data/datasets/users/{tenant_id}/{user_id}/<name>/` (per-user) |

### R3 — Cutover

No migration. Pre-existing rows with `tenant_id IS NULL` are wiped. Phase D ships:

- `scripts/wipe_clean.py` — destructive, guarded by `SLM_FORGE_WIPE_CONFIRM=YES` env var; logs every action as structured JSON.
- `make wipe-clean` Makefile target wrapping the script.

`wipe_clean.py` MUST:
1. Truncate every SQLModel-mapped table by name. The full table list is enumerated explicitly (no `DROP TABLE *`) — see §6.
2. `rm -rf` the runtime artifact roots: `/app/runs`, `/app/exports`, `/app/storage`, `data/datasets/users/`.
3. List every Ozone bucket matching `^slm-forge-`; for each, delete + recreate empty. Buckets that don't match the prefix are skipped (defence against misconfigured `SLM_FORGE_OZONE_BUCKET_PREFIX`).
4. Re-create the schema by calling `apps/api/services/db.py::init_db()`.
5. Re-run `scripts/seed_datasets.py` so bundled samples are restored under `data/datasets/global/`.

After cutover, the schema can tighten: `tenant_id` and `user_id` columns become NOT NULL on `Run`, `TrainingSession`, `Export`, `Metric`, `HermesTrace`, `AutoFixAttempt`. SQLite cannot ALTER nullability in place; `init_db()` creates the new schema fresh on a wiped DB.

### R4 — Worker boundary

Workers (`packages/trainer`, `packages/ratchet`, `packages/exporter`) are system-level by design — they MUST be able to claim runs from any tenant for any user. The current claim path (`apps/api/services/claims.py:152`) is correct: filter by `status=QUEUED` AND `trainer_backend=<backend>`, no tenant clause.

When a worker uploads an artifact, the object-store key MUST be derived from the **claimed Run's** `tenant_id` + `user_id`, not the worker's own (`/tenants/system`) identity. Verified in: `packages/trainer/runner.py`, `packages/exporter/pipeline.py`, ratchet's run-creation path.

Worker-side trace writes (`packages/ratchet/loop.py`, `apps/api/services/tracing.py:trace_span` when invoked from worker contexts) MUST stamp `HermesTrace.tenant_id` and `HermesTrace.user_id` from the parent `Run` row, not from `current_tenant()`.

### R5 — Datasets

Filesystem layout post-Phase-D:

```
data/
  datasets/
    global/                       # bundled samples, read-only, visible to all
      <name>/...
    users/
      {tenant_id}/
        {user_id}/
          <name>/...              # user-uploaded datasets
```

API endpoints behave as:

- `GET /api/v1/datasets` — returns `global/*` ∪ `users/{Identity.tenant_id}/{Identity.user_id}/*`; admin role expands the second branch to all users in their tenant.
- `POST /api/v1/datasets/...` (ingest endpoints) — writes to `users/{Identity.tenant_id}/{Identity.user_id}/<safe_name>/`. Rejects dataset names containing `..`, `/`, or absolute paths with HTTP 400.
- `GET /api/v1/datasets/{name}` and download endpoints — resolve `<name>` against the same union; 404 if not in caller's visible set.

`scripts/seed_datasets.py` writes to `data/datasets/global/` only.

### R6 — Test coverage

Failing-first tests (TDD red), then GREEN. Coverage floor 90% on the touched routers, measured by line + branch.

| Test file | Asserts |
|---|---|
| `tests/api/test_runs_isolation.py` | alice@acme cannot list/get/patch/delete/download admin@local's runs (404, not 403 — opaque). |
| `tests/api/test_sessions_isolation.py` | Same for sessions. |
| `tests/api/test_exports_isolation.py` | Same for exports, including the file-stream download endpoint. |
| `tests/api/test_metrics_isolation.py` | Metrics list under a foreign run returns 404 (parent run invisible); metrics POST denied for non-owner. |
| `tests/api/test_traces_isolation.py` | After `user_id` is added, non-admin sees only own traces; admin sees tenant-wide. |
| `tests/api/test_datasets_isolation.py` | Per-user upload visible only to owner + tenant admin; `global/` always visible. |
| `tests/api/test_admin_sees_tenant_wide.py` | Admin role sees all in tenant; never cross-tenant. |
| `tests/api/test_create_stamps_identity.py` | Create endpoints stamp `tenant_id`/`user_id`; client-supplied values are ignored. |
| `tests/api/test_patch_rejects_identity_mutation.py` | `*Patch` cannot mutate `tenant_id`/`user_id` (422 if present). |
| `tests/api/test_worker_claims_across_tenants.py` | Worker JWT claims runs regardless of `Run.tenant_id`. |
| `tests/api/test_worker_artifact_path.py` | Uploaded artifact key uses `Run.tenant_id`/`Run.user_id`, not the worker's. |
| `tests/scripts/test_wipe_clean.py` | Refuses without `SLM_FORGE_WIPE_CONFIRM=YES`; truncates listed tables; preserves bundled `global/` samples; re-seeds; idempotent on second run. |

### R7 — Non-goals

- No user-to-user sharing of runs/datasets (no opt-in share UI).
- No per-user storage quotas or rate limits.
- No tenant deletion / user offboarding cascades.
- No soft delete / archival.
- No audit log of data access (defer to Phase E observability work).
- No `Dataset` SQLModel — filesystem layout only.
- No migration / backfill — clean slate by user's explicit choice.

---

## 3. Interfaces

No new public API endpoints. All changes are internal:

- Same route surface for `runs`, `sessions`, `exports`, `metrics`, `traces`, `datasets`.
- `*.Patch` Pydantic models gain explicit field omissions for `tenant_id` / `user_id` (Pydantic `model_config = {"extra": "ignore"}` silently drops client-supplied values; explicit field absence is enough).
- One new Makefile target: `make wipe-clean`.
- One new script: `scripts/wipe_clean.py`.

---

## 4. Constraints

- **Backwards compatibility:** intentionally broken. This is a clean-slate cutover (per user's choice) — the user runs `make wipe-clean` once during the upgrade. Release notes call this out as **BREAKING**.
- **SQLite:** column nullability changes in place are not supported; clean wipe is the only sound path. (Postgres would allow `ALTER COLUMN ... SET NOT NULL` with a backfill, but the lab targets SQLite.)
- **Ozone bucket lifecycle:** delete-then-recreate is destructive on shared buckets. The script guards with the `slm-forge-` prefix check and the `SLM_FORGE_WIPE_CONFIRM` env var.
- **`auth ENABLED=false` mode:** the synthetic admin (`apps/api/services/identity.py`) still has `tenant_id="local"`, `user_id="local-admin"`, `role="admin"`, `is_admin=True`. In disabled-auth mode the SPA continues to see all `local`-tenant rows (which is correct dev-mode behaviour).

---

## 5. Data model deltas

```python
# apps/api/models/hermes_trace.py — additive
class HermesTrace(SQLModel, table=True):
    ...
    tenant_id: str = Field(default="default", index=True)
    user_id: str = Field(default="default", index=True)   # NEW (Phase D)
```

After `make wipe-clean`, the following columns are functionally NOT NULL on a fresh DB (no row will be inserted without them, by router contract):

- `runs.tenant_id`, `runs.user_id`
- `training_sessions.tenant_id`, `training_sessions.user_id`
- `exports.tenant_id`, `exports.user_id`
- `metrics.tenant_id`, `metrics.user_id`
- `hermes_traces.tenant_id`, `hermes_traces.user_id`
- `auto_fix_attempt.tenant_id`, `auto_fix_attempt.user_id`

We do not mark them `nullable=False` in SQLModel because SQLite migrations of existing columns are brittle; the **router contract** is the gate. A test (`test_create_stamps_identity.py`) pins it.

---

## 6. Tables wiped by `wipe-clean`

Explicit list (no DROP TABLE wildcards):

```
runs
training_sessions
exports
metrics
hermes_traces
auto_fix_attempt
chat_conversations
chat_messages
heartbeat                    # ephemeral worker status — also flushed
```

Re-created by `init_db()` after truncation. The script is idempotent — re-running on an empty DB is a no-op for the truncate step.

---

## 7. Acceptance criteria

The phase is "done" when all of the following are true:

- [ ] All 12 isolation/stamping tests written first, all GREEN.
- [ ] `uv run pytest -q` passes the full suite.
- [ ] `uv run ruff check` clean on every file Phase D touched.
- [ ] `uv run mypy apps packages` clean on every file Phase D touched.
- [ ] Manual UI smoke: alice@acme creates a run; bob@acme cannot see it; admin@acme can; carol@globex cannot.
- [ ] `make wipe-clean` runs cleanly twice in a row (idempotent).
- [ ] Release notes `release/0.9.0.md` populated.
- [ ] `README.md` updated with `make wipe-clean` and the per-user dataset layout.
- [ ] One Conventional Commit (`feat(tenancy)!:` — breaking) via `commit_message.md`.
- [ ] Change summary + release link + curl/UI verification steps delivered to user.

---

## 8. Out of scope (deferred backlog)

- User offboarding flow (delete user → cascade purge their data).
- Tenant offboarding flow.
- Sharing / collaboration UI.
- Per-user storage quotas.
- Audit log (who accessed what, when).
- Migration toolkit for ops who want to preserve old data (would need a new owner mapping spec).