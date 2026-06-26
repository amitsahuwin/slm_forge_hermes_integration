# Policy & Role Reference

Quick reference for SLM-Forge's authorization system.

## User → Role Mappings (Keycloak)

| Username | Password | Role | Description |
|----------|----------|------|-------------|
| `admin@local` | `admin1234` | **admin** | Full CRUD across all resources |
| `engineer@local` | `engineer` | **data_engineer** | Owns datasets, experiments, runs |
| `expert@local` | `expert123` | **domain_expert** | Read-only on technical artifacts; authors research |
| `devops@local` | `devops123` | **devops** | Manages logs and system settings |
| `ops@local` | `ops12345` | **operations** | Day-to-day runtime ops |
| `support@local` | `support1` | **support** | Read-only across the board |

## Role → Permissions Matrix (OPA)

| Role | Dataset | Experiment | Run | Export | Log | Setting | Research | Chat |
|------|---------|------------|-----|--------|-----|---------|----------|------|
| **admin** | CRUD | CRUD | CRUD+execute+export | CRUD+execute | CRUD | CRUD | CRUD | CRUD |
| **data_engineer** | CRUD | CRUD | R+update | R+execute | R | - | R | RW |
| **domain_expert** | R+update | R | R | R | - | - | CRUD | RW |
| **devops** | - | - | R | - | CRUD | CRUD | R | R |
| **operations** | R | R | R | R+execute | R | - | R | R |
| **support** | R | R | R | R | R | R | R | R |

**Legend:** R=read, C=create, U=update, D=delete

## Why Both?

| Aspect | Keycloak | OPA |
|--------|----------|-----|
| **Concern** | Authentication | Authorization |
| **Question** | Who are you? | What can you do? |
| **Stores** | Users, passwords, role assignments | Permission rules/policies |
| **Output** | JWT tokens with roles | Allow/Deny decisions |
| **Changes** | Requires user/role updates in Keycloak UI | Edit .rego files, hot-reloads |
| **Flexibility** | User management | Complex policy logic |

## How They Work Together

```
1. User logs in → Keycloak
   ↓
2. Keycloak returns JWT with roles: ["data_engineer"]
   ↓
3. User makes API request with JWT → FastAPI
   ↓
4. FastAPI middleware verifies JWT with Keycloak (is token valid?)
   ↓
5. FastAPI extracts user + roles from JWT
   ↓
6. @requires("delete", "export") decorator asks OPA:
   "Can user with roles=['data_engineer'] perform 'delete' on 'export'?"
   ↓
7. OPA checks role_matrix.rego → returns {"result": false}
   ↓
8. FastAPI returns 403 with reason: "role(s) ['data_engineer'] lack 'delete' on 'export'"
```

## Quick Commands

```bash
# View all mappings
./view_policies.sh all

# View just users
./view_policies.sh users

# View just roles
./view_policies.sh roles

# View permissions matrix
./view_policies.sh matrix

# Test OPA policies
./view_policies.sh test

# Get Keycloak admin info
./view_policies.sh keycloak
```

## Manual OPA Query

Test a specific permission:

```bash
curl -fsS -X POST http://localhost:8181/v1/data/slm_forge/allow \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "user": {"id": "engineer@local", "roles": ["data_engineer"]},
      "action": "delete",
      "resource": "dataset"
    }
  }'
```

Get denial reason:

```bash
curl -fsS -X POST http://localhost:8181/v1/data/slm_forge/reason \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "user": {"id": "support@local", "roles": ["support"]},
      "action": "update",
      "resource": "dataset"
    }
  }'
```

## Keycloak Admin Console

- **URL**: http://localhost:8080
- **Username**: `admin`
- **Password**: `admin`
- **Realm**: `slm-forge`

Navigate to:
- **Users**: Realm Settings → Users
- **Roles**: Realm Settings → Roles
- **Role Mappings**: Users → [select user] → Role Mappings

## Policy Files

- **Role Matrix**: `policies/role_matrix.rego`
- **Main Policy**: `policies/slm_forge.rego`
- **Policy Tests**: `policies/slm_forge_test.rego`
- **User Definitions**: `keycloak/realm-export.json`

## Testing Policies

```bash
# Run OPA unit tests
opa test policies/

# Or use the Docker container
docker exec slm-forge-opa opa test /policies
```

## Resources

- Full auth documentation: `docs/AUTH.md`
- Action vocabulary: `read`, `create`, `update`, `delete`, `execute`, `export`
- Resource vocabulary: `dataset`, `experiment`, `run`, `export`, `log`, `setting`, `research`, `chat`
