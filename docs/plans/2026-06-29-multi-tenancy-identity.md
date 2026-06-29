# Plan — Phase C: Multi-tenancy + identity

> **Spec:** `docs/specs/2026-06-29-multi-tenancy-identity.md`
> **Date:** 2026-06-29 · **Owner:** Amit
> **Branch:** `feat/multi-tenancy-identity` off `main` after Phase B.
> **Parent plan:** `/Users/amitsahu/.claude/plans/hazy-stargazing-spindle.md`

---

## Honest framing

This is the **invasive** PR. It changes how every request is
authorized, adds 5 column migrations, rewrites every list query, and
forces a Keycloak login for everyone — including local dev.

We mitigate that with three things:

1. The migrations are additive and reversible.
2. We provide a `make auth-token` helper that mints a long-lived dev JWT
   for `alice@acme/admin`, so iteration cost is one `eval $(make
   auth-token)` per terminal.
3. Auth-disable mode is removed only **after** the seed JWT helper is
   in place and verified end-to-end.

---

## Red-team passes

### Pass 1 — query scoping completeness
- **Concern:** missing a single `select(Run)` call elsewhere in the
  codebase leaks every tenant's runs.
- **Mitigation:** `rg "select\((Run|TrainingSession|Export|Metric|AutoFix)\)"
   apps/ packages/` produces an exhaustive checklist; every site gets
  the one-line `scope_query` change; a `tests/api/test_tenancy_isolation.py`
  test iterates every list endpoint and asserts non-admin tenant B sees
  zero of tenant A's rows.

### Pass 2 — worker capability scope
- **Concern:** a compromised worker could call `GET /runs` and
  enumerate every tenant.
- **Mitigation:** OPA `tenant_isolation.rego` only permits the worker
  role for `claim_run`, `update_run`, `upload_artifact`, `read_dataset`.
  All other endpoints (including list endpoints) reject the worker
  role with 403. `test_worker_token.py::test_worker_cannot_list_runs`.

### Pass 3 — backfill correctness
- **Concern:** existing rows have `tenant_id=NULL`; any query that
  forgets to filter `NULL OUT` will return them across tenants.
- **Mitigation:** the backfill happens at migration time, setting
  `tenant_id='_legacy_'`. The `scope_query` helper hard-rejects an
  Identity whose `tenant_id == '_legacy_'`. Legacy rows become readable
  only via a one-off `make migrate-claim-legacy TENANT=X USER=Y` admin
  CLI.

### Pass 4 — OPA bundle drift
- **Concern:** Rego policies live in `policies/` but OPA loads them
  from a bundle. Forgetting to update the bundle silently keeps the
  old policy active.
- **Mitigation:** docker-compose mounts `policies/` directly in dev;
  in prod, `make opa-bundle` is wired into `make ci`. `policies/.last_bundle_sha`
  ensures CI fails if the bundle is stale.

### Pass 5 — clean (target)
- Identity + scoping helpers are tiny, well-tested abstractions.
- OPA tests cover the full matrix.
- Workers operate within their narrow scope.
- Auth-disable mode is gone — no foot-gun.

---

## Implementation steps

### Step 1 — Tests (RED)

Create the five test files in spec §R9. Run them; all must fail.

### Step 2 — R1: Identity + R2: migrations

Order matters — `apps/api/services/identity.py` first so the tests can
import it, then `apps/api/deps.py`, then DB migrations in
`apps/api/services/db.py` and matching SQLModel fields in
`apps/api/models/{run,session,export,metric,autofix}.py`.

Run `python -m apps.api.services.db` (or hit `/health` which triggers
init) on a copy of the existing SQLite — verify migrations apply and
the schema is correct via `sqlite3 ... ".schema runs"`.

### Step 3 — R3: scoping + R6: remove disable

`apps/api/services/scoping.py`, then walk every router with the
`rg select(...)` grep and apply `scope_query` everywhere. Remove the
auth-disable short-circuit in `apps/api/middleware/auth.py:252`.

### Step 4 — R4: worker identity

`packages/common/auth.py`, then refactor each worker's HTTP client to
use the token. `packages/trainer/runner.py`, `packages/ratchet/loop.py`,
`packages/exporter/pipeline.py`.

### Step 5 — R5: OPA + R8: realm seed

Update Rego policies (locate via `ls policies/`). Update Keycloak
realm-export JSON (locate via `rg -l "realm-export\|realm.json"
deploy/ docker-compose*`). Add the `slm-forge-worker` client.

### Step 6 — R7: frontend

`apps/web/src/auth/AuthContext.tsx` + the tenant pill component.

### Step 7 — verify

```bash
make opa-test
uv run pytest -q
cd apps/web && npm run build && npm test
uv run ruff check --fix
uv run mypy apps packages

# end-to-end
make dev
make auth ENABLED=true            # the only mode now
make auth-token EMAIL=alice@acme  # writes JWT to /tmp/slm_forge_dev.jwt
curl -H "Authorization: Bearer $(cat /tmp/slm_forge_dev.jwt)" http://localhost:8000/api/v1/runs
```

### Step 8 — commit + PR

PR title: `feat: multi-tenancy + identity (Keycloak groups → tenant)`.

PR body includes a migration runbook: "anyone with local data must
either re-run their experiments or run `make migrate-claim-legacy
TENANT=acme USER=<email>` to claim legacy rows."

---

## Files modified

- `apps/api/services/db.py` (add 5 migration lists)
- `apps/api/services/identity.py` (new)
- `apps/api/services/scoping.py` (new)
- `apps/api/deps.py` (new)
- `apps/api/middleware/auth.py` (remove disable)
- `apps/api/models/{run,session,export,metric,autofix}.py`
- All routers under `apps/api/routers/` with `select(Run|TrainingSession|Export|Metric|AutoFix)`
- `packages/common/auth.py` (new)
- `packages/{trainer,ratchet,exporter}/*` (attach Bearer)
- `policies/role_matrix.rego`
- `policies/tenant_isolation.rego` (new)
- `policies/slm_forge.rego`
- `policies/slm_forge_test.rego` (extended)
- Keycloak realm-export JSON (extended)
- `apps/web/src/auth/AuthContext.tsx`
- Top nav component (tenant pill)
- `Makefile` (`auth`, `auth-token`, new `migrate-claim-legacy`)
- `.env.example` (new SLM_FORGE_WORKER_* vars)
- `docs/specs/2026-06-29-multi-tenancy-identity.md`
- `docs/plans/2026-06-29-multi-tenancy-identity.md`
- `release/PR-3.md` (new)

## Definition of Done

- [ ] Spec + plan committed
- [ ] All 5 test files green
- [ ] Coverage ≥90% on `identity.py`, `scoping.py`, `deps.py`,
      `packages/common/auth.py`
- [ ] OPA matrix ≥20 cases green
- [ ] Manual: 2 tenants × admin/non-admin matrix passes acceptance criteria
- [ ] `make auth ENABLED=false` refuses to start
- [ ] Workers boot, fetch JWT, claim runs, upload artifacts
- [ ] PR opened; release/PR-3.md written
