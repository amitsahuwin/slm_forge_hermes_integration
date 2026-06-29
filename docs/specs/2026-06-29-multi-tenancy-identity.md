# Phase C Spec — Multi-tenancy + identity

> **Status:** approved · **Date:** 2026-06-29 · **Owner:** Amit
> **Plan:** `docs/plans/2026-06-29-multi-tenancy-identity.md`
> **Branch:** `feat/multi-tenancy-identity` off `main` after Phase B.

---

## 1. Problem

The lab has no real tenant isolation:

- `Run`, `Session` (`apps/api/models/{run,session}.py`), `Export`,
  `Metric`, `AutoFix` carry **no** `tenant_id` / `user_id` / `role`
  columns. List handlers return global state regardless of who is
  logged in (`sessions.py:70`, `runs.py`).
- OPA policies (`policies/role_matrix.rego`, `slm_forge.rego`) are
  role-based-only — no `same_tenant` or `same_owner` checks.
- Workers (`packages/{trainer,ratchet,exporter}`) have no identity at
  all; they call the API by name only. Once auth becomes mandatory
  they would lose access entirely.
- The auth-disable mode (`make auth ENABLED=false`) silently makes the
  entire dataset visible to anyone; this contradicts CLAUDE.md §35
  ("data isolation is non-negotiable").

`hermes_traces` and `chat_*` already carry `tenant_id`/`user_id`
(set to `'default'`); we reuse that column shape verbatim.

---

## 2. Requirements

### R1 — Identity

New module `apps/api/services/identity.py` defines the canonical record:

```python
@dataclass(frozen=True)
class Identity:
    tenant_id: str       # from JWT `groups[0]` stripped of leading "/"
    user_id: str         # from JWT `sub`
    email: str | None
    role: str            # highest realm role in role_matrix order
    scopes: frozenset[str]  # e.g. {"worker:claim_run", "worker:upload_artifact"}
    is_admin: bool       # convenience: role == "admin"
    is_worker: bool      # convenience: "worker:*" in scopes
```

New FastAPI dependency in `apps/api/deps.py`:

```python
def current_identity(request: Request) -> Identity:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, detail="auth required")
    return Identity.from_keycloak_user(user)
```

Role precedence in `role_matrix.rego`:
`admin > devops > data_engineer > domain_expert > operations > support > viewer > worker`.

### R2 — Tenant columns on data tables

Additive migrations in `apps/api/services/db.py`:

- `_RUN_MIGRATIONS` += `[("tenant_id","TEXT"),("user_id","TEXT"),("role","TEXT")]`
- `_SESSION_MIGRATIONS` same.
- New `_EXPORT_MIGRATIONS`, `_METRIC_MIGRATIONS`, `_AUTOFIX_MIGRATIONS`
  with the same three columns; wired into `init_db()`.

Existing rows backfill all three to `NULL`. New rows MUST populate;
a post-backfill `NOT NULL` contract migration ships in a follow-up
phase once the backfill job confirms zero NULLs.

### R3 — Scoping helper

New module `apps/api/services/scoping.py`:

```python
def scope_query(query, identity: Identity, model):
    """
    Add WHERE clauses to a SQLModel select() to scope to the caller.
    - admin in tenant X: sees all rows where tenant_id == X.
    - non-admin: sees only rows where tenant_id == X AND user_id == identity.user_id.
    - worker: sees only rows where claimed_by == identity.user_id (worker name).
    """
```

Every list/get/update/delete handler for `Run`, `TrainingSession`,
`Export`, `Metric`, `AutoFix` becomes a one-line change.

### R4 — Worker service identity

New module `packages/common/auth.py`:

```python
class WorkerToken:
    def __init__(self, keycloak_url, realm, client_id, client_secret): ...
    async def bearer(self) -> str:  # cached until exp - 60s
    async def refresh(self) -> None: ...
```

Each of `packages/{trainer,ratchet,exporter}` boots, instantiates
`WorkerToken`, and attaches the JWT to every API call. Required env:

- `SLM_FORGE_KEYCLOAK_URL`
- `SLM_FORGE_KEYCLOAK_REALM`
- `SLM_FORGE_WORKER_CLIENT_ID` (default `slm-forge-worker`)
- `SLM_FORGE_WORKER_CLIENT_SECRET` (required, no default)

Worker JWT carries `realm_role=worker`, `groups=[/tenants/system]`.
Scopes granted by Keycloak client role: `claim_run`, `update_run`,
`upload_artifact`, `read_dataset`. Workers cannot list other tenants'
runs/sessions — those endpoints check `Identity.is_admin` AND
`not is_worker` before exposing cross-user data.

### R5 — OPA policies

- `policies/role_matrix.rego`: add `worker` row with the four scopes above.
- New `policies/tenant_isolation.rego`:
  ```
  package slm_forge.tenancy
  same_tenant if input.user.tenant_id == input.resource.tenant_id
  same_owner  if input.user.user_id   == input.resource.user_id
  allow if same_tenant; same_owner
  allow if same_tenant; input.user.is_admin
  allow if input.user.is_worker; input.resource.claimed_by == input.user.user_id
  ```
- `policies/slm_forge.rego`: combine role-matrix and tenancy rules in
  the top-level `allow`.
- `policies/slm_forge_test.rego` (extended): full matrix.

### R6 — Remove auth-disable mode

`apps/api/middleware/auth.py:252`: drop the `enforce=False` short-circuit.
On boot, if `SLM_FORGE_AUTH_ENABLED=false`, log a deprecation warning and
`raise SystemExit(2)`. `Makefile`'s `auth` target updated accordingly.

### R7 — Frontend identity context

`apps/web/src/auth/AuthContext.tsx` (extend `AppUser`):

```ts
type AppUser = {
  id: string;
  email: string;
  roles: string[];
  tenant_id: string;
  primary_role: string;
};
```

Populate `tenant_id` from JWT `groups[0]`, `primary_role` from the
role-matrix precedence client-side (kept in sync with the server via
`apps/web/src/lib/roles.ts`).

A tenant pill renders in the top nav (`apps/web/src/components/TopBar.tsx`
or equivalent — pinpointed at implementation time).

### R8 — Keycloak realm seed

Realm export JSON (location confirmed during implementation):

- 2 demo tenants: `acme`, `globex`.
- 3 user roles per tenant: `admin`, `data_engineer`, `viewer`.
- 2 users per role per tenant = 12 users total.
- 1 confidential client `slm-forge-worker` with `client_credentials`
  grant and a single role `worker`.

### R9 — Tests

- `tests/api/test_identity_resolution.py` — JWT → Identity mapping.
- `tests/api/test_tenancy_isolation.py` — 2 tenants × 2 users; cross
  tenant/user access denied (403); admin sees both users; worker can
  upload to claimed run.
- `tests/api/test_worker_token.py` — token cache + refresh.
- `policies/slm_forge_test.rego` — extended matrix (≥20 cases).
- `apps/web/src/auth/__tests__/AuthContext.test.tsx` — `tenant_id`
  populated from `groups[0]`.

---

## 3. Non-goals

- Cross-tenant admin UI (super-admin console deferred).
- Per-tenant resource quotas (deferred).
- Cost accounting per tenant (deferred).
- Tenant deletion / GDPR right-to-erasure (deferred to its own phase).

---

## 4. Acceptance criteria

- All R9 tests written first; all green.
- Manual: log in as `alice@acme` → create a run → log out → log in as
  `bob@acme` (non-admin) → GET /runs returns empty → GET /runs/<alice's id>
  returns 403.
- Log in as `admin@acme` → GET /runs returns alice's + bob's.
- Log in as `alice@globex` → GET /runs returns empty (different tenant).
- Worker boots → fetches JWT → claims a queued run → uploads an
  artifact — all succeed.
- `make opa-test` green; `uv run pytest -q` green; coverage ≥90% on
  changed modules.
- `make auth ENABLED=false` refuses to start with a clear error.
