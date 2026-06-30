# ADR-0007 — Per-user isolation enforcement + clean-slate cutover

- **Status:** Accepted
- **Date:** 2026-06-30
- **Phase:** D (per-user isolation)
- **Related:** ADR-0006 (multi-tenancy via Keycloak groups), `docs/specs/2026-06-30-phase-d-user-isolation.md`, CLAUDE.md §35 ("data isolation is non-negotiable")

## Context

Phase C added `tenant_id` + `user_id` columns and a `scope_query()` helper, but did not wire them into the `runs`, `sessions`, `exports`, `metrics`, or `traces` routers. The audit (recorded in the Phase D spec, §1) showed those endpoints leak across both the tenant and the user axes. `HermesTrace` rows further lack a `user_id` column entirely, leaving them tenant-scoped only.

Three decisions were required:

1. **How to close the leak** — extend `scope_query` to more entities, or call it from the existing routers as-is?
2. **What is the visibility model** — per-user with admin-sees-tenant-wide, or per-user even for admins, or per-tenant only?
3. **How to handle pre-existing rows** with `NULL` `tenant_id` — migrate, soft-purge, or wipe?

## Decision

### 1. Reuse `scope_query` verbatim — wire it into every router

`apps/api/services/scoping.py:26` already implements the exact predicate Phase D needs:

```python
WHERE tenant_id = identity.tenant_id
  AND (identity.is_admin OR user_id = identity.user_id)
  AND (NOT identity.is_worker)
```

Workers get `WHERE 1=0` — they cannot enumerate. The phase D job is to call this helper from every list/get/patch/delete site that touches a tenanted model. No new helper, no abstraction layer.

`@requires(...)` (RBAC, OPA-backed) stays untouched. RBAC and tenant-scoping are orthogonal — RBAC says "may this role perform this action on this resource type", scoping says "which rows of that type belong to this caller". Defence in depth: RBAC + query scope + storage bucket isolation (`slm-forge-{tenant_id}` Ozone buckets, already in place from Phase C).

### 2. Admin scope is tenant-wide (within their own tenant)

`admin@local` sees every row in tenant `local`. `admin@local` does NOT see anything in tenant `acme`. Within a tenant, non-admin roles (`data_engineer`, `domain_expert`, `viewer`, ...) see only their own rows.

This matches SaaS multi-tenancy norms (a tenant admin owns the tenant; the platform owns the cross-tenant view). It also matches `scope_query`'s existing implementation — no special-case code.

### 3. Clean-slate cutover, no migration

Pre-Phase-D rows have `tenant_id IS NULL`. SQLite cannot tighten column nullability in place, and the user does not value preserving lab history that pre-dates the boundary. Phase D ships `scripts/wipe_clean.py` (env-guarded), which truncates the SQLModel tables, removes runtime artifact roots, deletes-and-recreates Ozone buckets matching `^slm-forge-`, re-creates the schema via `init_db()`, and re-seeds bundled sample datasets.

The release is marked **BREAKING** (`feat(tenancy)!:`). Users running 0.8.0 must run `make wipe-clean` once on upgrade.

## Rejected alternatives

### A. Add a `same_tenant` / `same_owner` rule to OPA

Move the boundary into Rego. Cleaner separation in theory, but: (a) OPA decisions are per-action, not per-row — list endpoints would still need server-side filtering anyway; (b) ABAC over OPA needs every row's `tenant_id` and `user_id` shipped into the policy context, which is expensive; (c) the policy engine is fail-open if unreachable in `auth ENABLED=false` mode, and we cannot accept fail-open on data isolation. Query-layer enforcement is the safer default; OPA can layer additional constraints later if needed.

### B. Postgres schemas (one schema per tenant)

True isolation, no SQL injection risk, easy bulk operations. But: (a) the lab targets SQLite for local-first dev; (b) schema-per-tenant doesn't scale past O(100) tenants in either Postgres or SQLite; (c) cross-tenant analytics (in scope for the "system" role / `tenants/system` worker) requires a fan-out query. Pushed to a future Phase if/when we move to Postgres in production.

### C. Owner-only visibility (admin sees only own rows)

Considered. Rejected because tenant admins need to debug failed runs, inspect cross-user metrics, and produce tenant-level summaries — none possible if their view is owner-only. The user explicitly confirmed tenant-wide admin scope.

### D. Migrate existing rows (assign all NULL-tenant rows to `admin@local`)

Considered. Rejected because (a) the user explicitly preferred a clean slate; (b) attribution is guessing — most pre-Phase-D lab activity was experimental and may bias the new system if surfaced as "production" runs; (c) the migration script is one-off, error-prone code we'd carry forever.

### E. Per-tenant scoping only (skip per-user)

Considered. Rejected because non-admin users (`viewer`, `data_engineer`) in a shared tenant should not see each other's runs by default — that's the user's literal ask. `scope_query` already supports both modes via `is_admin`; using both axes costs nothing.

## Consequences

**Positive:**
- A single, type-checked enforcement point (`scope_query`) — easy to audit and to test.
- The Ozone bucket layer continues to enforce isolation independently — defence in depth.
- Workers retain their cross-tenant claim ability (system-level by design); their JWT carries `/tenants/system` and `role=worker`, which `scope_query` translates into `WHERE 1=0` (no enumeration), while the explicit claim endpoint is wide.
- Clean schema after wipe: every row born with the right `tenant_id` + `user_id`.

**Negative:**
- Lab history pre-dating Phase D is destroyed. Release notes flag this prominently.
- Adding `user_id` to `HermesTrace` is a column add — clean wipe absorbs it; otherwise it would have needed an additive migration.
- The contract test that walks router source via AST (looking for unscoped `select(Model)`) is best-effort; future routers added without scoping may slip past lint. Mitigation: a per-router isolation test is the real backstop.

**Neutral:**
- Frontend needs no changes — the lists it renders are already populated by the API. They will simply be scoped server-side.
- `make auth ENABLED=false` synthetic-admin behaviour is unchanged: `tenant_id="local"`, `user_id="local-admin"`, `is_admin=True`. Dev-mode loop continues to work.

## Verification

- ~12 failing tests landed first (TDD red), all green at end.
- `make wipe-clean && make seed-data && make dev` from a dirty checkout → `alice@acme` and `admin@local` see disjoint lists.
- `curl` cross-user probe in release notes returns 404.
- Coverage report ≥90% on touched routers.