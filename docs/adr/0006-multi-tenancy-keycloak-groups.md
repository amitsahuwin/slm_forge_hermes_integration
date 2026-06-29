# ADR-0006 — Multi-tenancy via Keycloak groups + `scope_query` + worker service accounts

- **Status:** Accepted
- **Date:** 2026-06-29
- **Phase:** C (multi-tenancy + identity)
- **Related:** `docs/specs/2026-06-29-multi-tenancy-identity.md`,
  ADR-0005 (trace nesting), CLAUDE.md §35 ("data isolation is
  non-negotiable")

## Context

The lab grew up single-tenant. `Run`, `TrainingSession`, `Export`,
`Metric`, `AutoFixAttempt` had no `tenant_id`/`user_id` columns; every
list endpoint returned global state regardless of who was logged in.
`hermes_traces` and `chat_*` carried a `tenant_id` column but it was
always set to `'default'`. Workers (`packages/trainer`,
`packages/ratchet`, `packages/exporter`) authenticated to the API via
a shared `X-Service-Token` that granted full admin — a leaked token
would have been full lab access.

Three independent decisions were needed:

1. **Where does the tenant boundary live?** Postgres schemas? A
   `tenants` table with foreign keys? A column on every row?
2. **How is tenant assigned at request time?** A custom header? A JWT
   claim? Keycloak groups?
3. **How do workers — which have no human identity — authenticate to
   the API once auth becomes mandatory?**

## Decision

- **One column on every user-data row** carries `tenant_id`. Same
  shape used by the pre-existing `hermes_traces`/`chat_*` columns;
  same indexes; same defaults. We do not introduce a `tenants` table
  yet — there is no per-tenant configuration that warrants one. When
  one appears, the column becomes a foreign key without a schema
  rewrite.
- **Keycloak groups are the tenant boundary.** A user's first
  non-empty group path segment (e.g. `/tenants/acme` → `acme`)
  becomes their `tenant_id`. Configuration lives where the
  authentication does — in the realm — rather than in a parallel
  per-app config.
- **`Identity` is the single mapping point.** `apps/api/services/identity.py`
  exposes a frozen dataclass derived from the JWT-backed `User`:
  ```python
  Identity(tenant_id, user_id, role, email, scopes, is_admin, is_worker)
  ```
  `current_identity` is the FastAPI dependency that every router
  consumes. A future identity-provider swap touches one file, not
  every router.
- **`scope_query(stmt, identity, Model)` is the only sanctioned
  way to read tenanted data.** A non-admin sees only their own rows
  in their tenant; an admin sees all rows in their tenant; a worker
  sees nothing (returns `WHERE 1=0`). The helper hard-errors when
  the model is missing `tenant_id`/`user_id` columns — a forgotten
  `scope_query` in a new router cannot become a silent cross-tenant
  leak.
- **Workers authenticate via Keycloak `client_credentials`.** A new
  confidential client `slm-forge-worker` with
  `serviceAccountsEnabled=true` issues JWTs in exchange for a client
  secret. `packages/common/auth.py:WorkerToken` fetches the JWT on
  boot, caches it until `exp - leeway`, refreshes lazily, and is
  preferred over the legacy `X-Service-Token` by `service_headers()`.
- **Worker identity is implied by `azp` (authorized party), not
  realm-role assignment.** `AuthMiddleware._build_user_from_claims`
  promotes any JWT whose `azp == SLM_FORGE_WORKER_CLIENT_ID` to
  `role=worker` + `groups=["/tenants/system"]`. This keeps the
  realm-export self-contained: a worker only needs the
  `serviceAccountsEnabled` flag — no client-role mappings or
  composite roles needed.
- **OPA enforces the same matrix in policy.** `policies/tenant_isolation.rego`
  exposes `same_tenant`, `same_owner`, `admin_in_tenant`,
  `worker_claim_match`, and a composite `tenant_allow`. The top-level
  `slm_forge.allow` now requires `tenant_allow` in addition to the
  role-matrix check. Backwards-compatible: when `input.context` lacks
  tenant/owner fields, the helpers return `true`, so non-tenant
  endpoints (settings, catalog) keep their matrix-only semantics.
- **Auth-disable mode is soft-deprecated, not removed.** A
  once-per-process WARN fires when `SLM_FORGE_AUTH_ENABLED=false`.
  The flag still works so existing tests (459 of them) pass; the
  path forward is `make auth ENABLED=true` plus `make auth-token
  EMAIL=alice@acme` for dev iteration. Hard removal is its own
  follow-up because it requires a per-test fixture refactor.

## Consequences

- **Pro — every row is its own scope check.** No app-wide "tenant
  switcher" state, no per-tenant connection pools, no Postgres-schema
  juggling. The tenant lives on the row; the query carries it.
- **Pro — the threading work is one-line.** Routers go from
  `select(Run).where(...)` to `scope_query(select(Run).where(...),
  identity, Run)`. A grep finds every site; an audit confirms it.
- **Pro — workers are first-class identities.** A compromised worker
  token grants exactly the worker scope (claim_run, update_run,
  upload_artifact, read_dataset) and nothing more, narrowly bounded
  by `worker_claim_match` for cross-tenant access. The legacy
  shared-secret bypass was a full-admin foot-gun; this isn't.
- **Pro — the realm-export is the single source of truth for who's
  in which tenant.** Reseeding the realm reseeds the tenants. No
  parallel "tenants.yaml" to drift.
- **Con — legacy NULL rows are invisible.** Existing
  `Run`/`Session`/`Export`/`Metric`/`AutoFix` rows have NULL
  `tenant_id`; `scope_query` refuses to surface them. The operator
  must either re-create the runs that matter or use a future
  `make migrate-claim-legacy TENANT=X USER=Y` script to claim them.
  We chose visibility-NULL over leak-NULL.
- **Con — capturing role at write time freezes it.** If a user is
  later demoted from `data_engineer` to `viewer`, their old runs
  still carry `role='data_engineer'`. This is intentional — artifact
  paths in Phase D include the role segment, and rewriting paths on
  every role change would be operationally fragile. The historical
  role is a fact about when the work was done.
- **Con — soft-deprecation of auth-disable extends the
  cutover.** Two code paths exist until the test suite is
  re-fixtured. Tracked in the same spec.

## Alternatives considered

1. **Per-tenant Postgres schema or database.** Strongest isolation
   but requires Postgres (we use SQLite), a per-tenant connection
   pool, dynamic schema selection on every request, and a heavy
   migration story. Rejected as overkill for a local-first lab whose
   "tenant" is more like "team folder."
2. **Tenant as a JWT custom claim instead of a group.** Equivalent
   information but requires per-realm protocol mapper configuration
   that drifts independently of the rest of the realm export.
   Keycloak groups are the conventional shape for this and ship in
   the default `groups` claim.
3. **Per-row encryption keyed by tenant.** Defends against database
   exfiltration but requires per-tenant key management and
   complicates every query. Rejected — the threat model is
   "another tenant's signed-in user shouldn't see my data," which
   row-level filtering plus OPA addresses.
4. **Hard-remove `SLM_FORGE_AUTH_ENABLED=false` now.** Cleanest end
   state but breaks the test suite immediately. Rejected in favour
   of a deprecation warning + dev-token helpers, with hard removal
   tracked separately.
5. **Service-account JWTs handed out by the API instead of Keycloak.**
   Simpler bootstrap but reinvents an identity provider. Rejected —
   the `client_credentials` grant is exactly the right OIDC primitive
   for service-account tokens.

## Implementation

- DB: `apps/api/services/db.py` adds three columns
  (`tenant_id`/`user_id`/`role`) to Run, TrainingSession, Export,
  Metric, AutoFixAttempt via additive nullable migrations.
- Identity: `apps/api/services/identity.py` (new) — `Identity`
  dataclass, role precedence, `_tenant_from_groups`,
  `current_identity` FastAPI dep.
- Scoping: `apps/api/services/scoping.py` (new) — `scope_query`.
- Middleware: `apps/api/middleware/auth.py` extracts
  `_build_user_from_claims` and promotes worker JWTs via `azp`.
- Worker tokens: `packages/common/auth.py` (new) — `WorkerToken`
  thread-safe cache. `packages/_api_client.py:service_headers()`
  prefers `Authorization: Bearer <JWT>` when
  `SLM_FORGE_WORKER_CLIENT_SECRET` is set; falls back to
  `X-Service-Token`.
- OPA: `policies/tenant_isolation.rego` (new),
  `policies/role_matrix.rego` (`viewer` + `worker` matrix entries),
  `policies/slm_forge.rego` (require `tenant_allow`).
- API: `apps/api/routers/auth.py` returns richer `MeResponse` with
  `tenant_id`/`primary_role`/`is_admin`/`is_worker`.
- Frontend: `apps/web/src/auth/keycloak.ts` `AppUser` extended;
  `apps/web/src/components/TenantPill.tsx` (new) in the nav.
- Realm seed: `keycloak/realm-export.json` — `+ viewer`, `+ worker`
  roles; `+ /tenants/{local,acme,globex,system}` groups; +6 demo
  users; + `slm-forge-worker` confidential client.
- Makefile: `make auth-token` accepts `EMAIL=`; new
  `make auth-worker-token`.
- Auth-disable mode: deprecation log in
  `apps/api/services/auth_settings.py`.
- Tests: `tests/api/test_identity_resolution.py` (7),
  `tests/api/test_scoping.py` (5), `tests/api/test_worker_token.py`
  (6), `tests/api/test_worker_identity.py` (3),
  `policies/slm_forge_test.rego` (+11 tenancy/worker/viewer cases).