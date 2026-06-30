# Plan — 2026-06-30 — Phase D: Per-user tenant isolation (clean-slate cutover)

> **Spec:** `docs/specs/2026-06-30-phase-d-user-isolation.md`
> **ADR:** `docs/adr/0007-per-user-isolation-cutover.md`
> **Release:** `release/0.9.0.md`
> **Branch:** `amitssahu` (current feature branch).

The spec answers *what*. This plan answers *how* and *in what order*. It assumes the spec's acceptance criteria are the gate.

---

## Build order

### Step 1 — Artifacts (this commit's first writes)

- [x] `docs/specs/2026-06-30-phase-d-user-isolation.md`
- [x] `docs/adr/0007-per-user-isolation-cutover.md`
- [x] `docs/plans/2026-06-30-phase-d-user-isolation.md` (this file)
- [ ] `release/0.9.0.md` — stub now, fill at the end.

### Step 2 — Failing tests (TDD red, before any router edit)

Land all 12 test files from spec §R6. Each test:
- Mints JWTs for two distinct users using the existing `auth-token` helper or the `mint_local_jwt` fixture in `tests/conftest.py` (verify present; add helper if missing).
- Drives the real router via `httpx.AsyncClient` against an in-process `app`.
- Asserts the 404/empty-list responses, not just status codes.

Run `uv run pytest tests/api/ -k "isolation or stamps or rejects_identity or worker_claim or worker_artifact" -v` — all must FAIL.

### Step 3 — Schema (HermesTrace)

- [ ] Add `user_id: str = Field(default="default", index=True)` to `HermesTrace`.
- [ ] No migration (clean wipe absorbs it). Bump `_SCHEMA_VERSION` constant in `apps/api/services/db.py` if one exists; otherwise note in release notes.
- Run `tests/api/test_traces_isolation.py` — schema-portion turns green.

### Step 4 — Router wiring

For each of `runs.py`, `sessions.py`, `exports.py`, `traces.py` (or its actual name; confirm before editing):

1. `create_*` endpoint: set `tenant_id` + `user_id` from `current_identity(request)` before `db.add()`. Drop any client-supplied values.
2. `list_*`: replace `select(Model)` with `scope_query(select(Model), identity, Model)`.
3. `get_*` / `patch_*` / `delete_*`: replace `db.get(Model, id)` with `select(Model).where(Model.id == id)` → `scope_query` → `.first()`. Return 404 (not 403) when not found.
4. `*Patch` Pydantic model: remove `tenant_id`/`user_id` fields if present; explicitly forbid via `model_config = {"extra": "forbid"}` (rejects unknown fields with 422). Verify existing tests don't pass these fields.
5. Metrics sub-endpoint on `runs.py`: derive `tenant_id`/`user_id` from the parent Run.
6. Export download endpoint: gate the file-stream response through the scoped lookup.

Per-router scoping tests turn green after this step.

### Step 5 — Datasets layout

- [ ] Update `scripts/seed_datasets.py` to write under `data/datasets/global/` (rename target dir; keep contents identical).
- [ ] `apps/api/routers/ingest.py`, `ingest_v2.py`, `datasets.py`, `datasets_detail.py`, `synth.py` — list aggregates `global/` ∪ scoped user dir; writes go to user dir; rejects path traversal (`..`, `/`, leading `.`).
- [ ] Helper: `apps/api/services/datasets_paths.py` — `user_dataset_root(identity) -> Path`, `visible_dataset_dirs(identity) -> list[Path]`. Used by all dataset routers.
- Tests `test_datasets_isolation.py` green.

### Step 6 — Worker artifact paths

- [ ] Verify `packages/trainer/runner.py`, `packages/exporter/pipeline.py`, ratchet's run-creation path read `Run.tenant_id`/`Run.user_id` (not worker identity) when assembling object-store keys.
- [ ] If they currently read worker identity, change to read `Run.tenant_id`/`Run.user_id` from the claim response (already returned by `/runs/claim`).
- [ ] Worker-side trace writes — `apps/api/services/tracing.py:trace_span` invoked from a worker context — derive `HermesTrace.tenant_id`/`user_id` from the parent `Run` row. May require threading the Run row through the worker's span-creation call.
- Tests `test_worker_claims_across_tenants.py` + `test_worker_artifact_path.py` green.

### Step 7 — Wipe-clean tooling

- [ ] `scripts/wipe_clean.py`:
  - Refuse without `SLM_FORGE_WIPE_CONFIRM=YES`.
  - Truncate the 9 tables in spec §6 (use SQLModel session + `DELETE FROM`).
  - `rm -rf` runtime dirs (configurable via env, defaults from `apps/api/services/db.py` and the storage factory).
  - Iterate Ozone buckets whose name matches `^slm-forge-`; delete + recreate empty. Skip if no Ozone configured.
  - `init_db()` (re-create schema).
  - Subprocess call to `python scripts/seed_datasets.py` so the global samples are restored.
  - Structured JSON log per destructive op.
- [ ] `make wipe-clean` Makefile target.
- [ ] `tests/scripts/test_wipe_clean.py` green.

### Step 8 — Contract / regression

- [ ] AST-walk test that flags any `select(Model)` in `apps/api/routers/` without an adjacent `scope_query` call (best-effort; uses `ast.NodeVisitor`). Documented escape hatch: comment `# scoping: not_needed reason=...` above the call.
- [ ] Verify `tests/api/test_realm_export_mappers.py` (existing) still passes — admin@local is now in `/tenants/local`, no cross-tenant exposure.
- Run full suite: `uv run pytest -q`.

### Step 9 — Lint + types

- [ ] `uv run ruff check --fix` on every file Phase D touched.
- [ ] `uv run mypy apps packages` — clean on touched modules.

### Step 10 — Manual UI smoke

- [ ] `make auth ENABLED=true` (with the Phase D tenant-pill fix from commit `960ee52` already in place).
- [ ] `SLM_FORGE_WIPE_CONFIRM=YES make wipe-clean`.
- [ ] `make dev` + `make trainer` in two terminals.
- [ ] Two browser sessions: alice@acme + admin@local. Alice creates a run; verify admin@local cannot see it; verify viewer@acme cannot see it; verify admin@acme can see it.

### Step 11 — Docs + release

- [ ] Fill `release/0.9.0.md` — Keep-a-Changelog format, BREAKING marker, impact + verification steps.
- [ ] Update `README.md` — new section on the clean-slate cutover and the per-user dataset layout. Add `make wipe-clean` to the make-targets table.
- [ ] Update `Makefile` help text for the new target.

### Step 12 — Commit

- [ ] Write `commit_message.md` (Conventional Commits `feat(tenancy)!:` with what + why).
- [ ] Stage explicit file list — no `git add -A`.
- [ ] `git commit -F commit_message.md`.
- [ ] Hand back to user: change summary + release link + curl/UI verify steps.

---

## Files touched (final picture)

**New:**
- `docs/specs/2026-06-30-phase-d-user-isolation.md` ✓
- `docs/plans/2026-06-30-phase-d-user-isolation.md` ✓
- `docs/adr/0007-per-user-isolation-cutover.md` ✓
- `release/0.9.0.md`
- `scripts/wipe_clean.py`
- `apps/api/services/datasets_paths.py`
- ~12 test files (`tests/api/`, `tests/scripts/`)

**Modified:**
- `apps/api/models/hermes_trace.py`
- `apps/api/routers/runs.py`, `sessions.py`, `exports.py`, `traces.py`
- `apps/api/routers/ingest.py`, `ingest_v2.py`, `datasets.py`, `datasets_detail.py`, `synth.py`
- `apps/api/services/db.py` (schema-version bump, no migration)
- `packages/trainer/runner.py`, `packages/exporter/pipeline.py`, ratchet write paths (only if they read worker identity today — verify)
- `scripts/seed_datasets.py`
- `Makefile`
- `README.md`

---

## Risk + mitigation

| Risk | Mitigation |
|---|---|
| Wipe runs against a production-like DB | Env-var guard + bucket-prefix safety check + structured logging of every action; refuse if any guard fails. |
| AST contract test produces false positives on a new router | Documented escape-hatch comment + per-router isolation test as the real backstop. |
| Worker uploads land in the wrong tenant bucket | Test `test_worker_artifact_path.py` pins the contract; storage factory key scheme (`{tenant}/{role}/{user}/...`) gives a visible failure mode. |
| `auth ENABLED=false` mode silently loses isolation | Synthetic admin remains `is_admin=True`, `tenant_id="local"` — dev-mode behaviour preserved; documented. |
| Pydantic `extra=forbid` breaks an existing client that passes harmless extra fields | Scan SPA fetches before flipping; if any send extra fields, switch to `extra=ignore` and log a warning. |