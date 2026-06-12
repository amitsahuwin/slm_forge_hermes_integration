# Authentication & Authorization (Phase M)

SLM-Forge ships with an **optional** identity + policy stack:

- **Keycloak** (`:8080`) issues OIDC JWT access tokens.
- **OPA** (`:8181`) decides whether `(user, action, resource)` is allowed.
- The FastAPI **AuthMiddleware** verifies the JWT against Keycloak's JWKS
  and attaches the resolved user to `request.state.user`.
- The **`@requires(action, resource)`** decorator gates destructive /
  admin endpoints by calling OPA at request time.

Everything is gated behind a single env var — `SLM_FORGE_AUTH_ENABLED`.
**Default is `false`.** With it off, every request gets a synthetic admin
user and no JWT/OPA work happens, so local-dev is bit-for-bit unchanged.

---

## TL;DR — enable enforcement

```bash
# 1. Bring up Keycloak + OPA alongside the existing stack.
docker compose --profile auth up -d keycloak opa

# 2. Wait ~30 s for Keycloak to finish realm import, then check:
curl -fsS http://localhost:8080/realms/slm-forge/.well-known/openid-configuration | jq .issuer
#   "http://keycloak:8080/realms/slm-forge"

curl -fsS http://localhost:8181/v1/data/slm_forge/allow \
  -d '{"input": {"user":{"id":"u","roles":["admin"]},"action":"delete","resource":"run"}}'
#   {"result": true}

# 3. Flip enforcement on for the API container.
SLM_FORGE_AUTH_ENABLED=true docker compose up -d api

# 4. Get a token + call the API.
TOKEN=$(curl -fsS -X POST \
  http://localhost:8080/realms/slm-forge/protocol/openid-connect/token \
  -d grant_type=password \
  -d client_id=slm-forge-web \
  -d username=engineer@local \
  -d password=engineer | jq -r .access_token)

curl -fsS -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
```

---

## Disabled mode (the default)

* `SLM_FORGE_AUTH_ENABLED=false` (or unset).
* `request.state.user` is `User(id="anonymous", roles=["admin"])`.
* `@requires(...)` is a no-op pass-through.
* Keycloak + OPA containers don't need to be running.
* `/api/v1/auth/me` returns the synthetic admin.
* `/api/v1/auth/config` returns `{auth_enabled: false, ...}` so the SPA
  knows to skip the login redirect.

This is the supported workflow for solo dev on a laptop. Use it.

---

## Enabled mode

When `SLM_FORGE_AUTH_ENABLED=true`:

1. The middleware demands `Authorization: Bearer <jwt>` on every
   non-public request. Public paths: `/`, `/api/v1/health`,
   `/api/v1/chat/health`, `/api/v1/auth/config`, `/metrics`, `/docs`,
   `/openapi.json`, `/redoc`.
2. JWTs are verified against Keycloak's JWKS (cached 5 min) using RS256.
   Issuer is checked, audience is **not** (Keycloak's `aud` claim is
   unreliable for resource-server access tokens).
3. The resolved `User` (with `roles` from `realm_access.roles`) is
   attached to `request.state.user`.
4. **Per-route policy enforcement is decorator-driven** —
   `@requires("delete", "run")` etc. Endpoints without the decorator
   remain accessible to any authenticated user. This lets us roll
   enforcement out incrementally.
5. If OPA is unreachable while enforcement is on, the decorator
   **fails closed** with `policy engine unreachable`.

---

## The role matrix (Phase M.3)

| Role            | Permissions                                                              |
|-----------------|--------------------------------------------------------------------------|
| `admin`         | CRUD on every resource                                                   |
| `data_engineer` | datasets:CRUD, experiments:CRUD, runs:R+cancel, exports:execute, logs:R, research:R, chat:RW |
| `domain_expert` | datasets:R+update_readme, experiments:R, runs:R, exports:R, research:RW, chat:RW |
| `devops`        | runs:R, logs:RW, settings:RW, research:R, chat:R                         |
| `operations`    | datasets:R, experiments:R, runs:R, exports:execute, logs:R, research:R, chat:R |
| `support`       | everything:R                                                             |

Source of truth: `policies/role_matrix.rego`. The main allow rule lives
in `policies/slm_forge.rego`. Edit either and OPA will hot-reload
because we pass `--watch /policies`.

---

## Currently-decorated endpoints

| Endpoint                                    | Decorator                       |
|---------------------------------------------|---------------------------------|
| `DELETE /api/v1/runs/{run_id}`              | `@requires("delete", "run")`     |
| `DELETE /api/v1/exports/{xid}`              | `@requires("delete", "export")`  |
| `POST   /api/v1/ingest/file`                | `@requires("create", "dataset")` |
| `POST   /api/v1/sessions`                   | `@requires("create", "experiment")` |
| `POST   /api/v1/synth/start`                | `@requires("create", "dataset")` |
| `DELETE /api/v1/research/reports/{file}`    | `@requires("delete", "research")` |
| `POST   /api/v1/admin/cleanup/execute`      | `@requires("update", "setting")` |
| `GET    /api/v1/auth/users`                 | `@requires("read", "setting")`   |

Read-mostly endpoints are intentionally left open during the rollout.
Add more `@requires(...)` as we tighten things up — the decorator is a
one-liner.

---

## Testing the policy

```bash
# Unit tests for the rego policy. Requires the `opa` CLI (or run inside
# the OPA container).
opa test policies/

# Sample interactive call to a running OPA:
curl -fsS -X POST http://localhost:8181/v1/data/slm_forge/allow \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "user": {"id": "bob", "roles": ["data_engineer"], "groups": []},
      "action": "delete",
      "resource": "export"
    }
  }'
# -> {"result": false}

curl -fsS -X POST http://localhost:8181/v1/data/slm_forge/reason \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "user": {"id": "bob", "roles": ["data_engineer"], "groups": []},
      "action": "delete",
      "resource": "export"
    }
  }'
# -> {"result": "role(s) [\"data_engineer\"] lack 'delete' on 'export'"}
```

---

## Seed users (from `keycloak/realm-export.json`)

| Username         | Password     | Role             |
|------------------|--------------|------------------|
| `admin@local`    | `admin1234`  | `admin`          |
| `engineer@local` | `engineer`   | `data_engineer`  |
| `expert@local`   | `expert123`  | `domain_expert`  |
| `devops@local`   | `devops123`  | `devops`         |
| `ops@local`      | `ops12345`   | `operations`     |
| `support@local`  | `support1`   | `support`        |

These are baked into the realm export for dev convenience — **rotate or
delete them before pointing this at anything resembling production.**

---

## Adding the `@requires` decorator to a new endpoint

```python
from fastapi import APIRouter, Request
from apps.api.middleware.auth import requires

router = APIRouter()

@router.delete("/{thing_id}", status_code=204)
@requires("delete", "thing")
def delete_thing(thing_id: int, request: Request) -> None:
    ...
```

The function **must** accept `request: Request` — the decorator pulls
`request.state.user` from it. Decorators run inside-out, so put
`@requires(...)` *after* (below) the `@router.delete(...)` line.

---

## Troubleshooting

* **401 "Missing bearer token"** — enforcement is on and you forgot
  `Authorization: Bearer ...`. Hit `/api/v1/auth/config` to confirm
  enforcement is actually enabled.
* **401 "Invalid bearer token"** — token expired, wrong realm, or
  Keycloak signing keys rotated. The JWKS cache is 5 minutes; restart
  the API to force a fresh fetch.
* **503 "Identity provider unreachable"** — Keycloak container is down.
* **403 "policy engine unreachable"** — OPA container is down. The
  decorator fails closed when enforcement is on; bring OPA back up.
* **403 "role(s) [...] lack '...' on '...'"** — OPA denied. Adjust the
  matrix or add the role to the user in Keycloak.
