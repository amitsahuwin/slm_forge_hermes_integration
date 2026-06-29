# Tenant isolation rules (Phase C).
#
# These are *additive* helpers consumed by the top-level `slm_forge.allow`
# policy. The middleware passes a tenant/owner block on `input.context`
# when the resource carries one — list endpoints attach it after looking
# up the row. Endpoints whose resource has no tenant scope (e.g.
# settings, system catalog) omit the block; the helpers then return
# `true` so the existing role-matrix decision is left untouched.
#
# Input contract:
#   input.user.id          — opaque user id (JWT sub)
#   input.user.tenant_id   — resolved by the API from Keycloak groups
#   input.user.roles       — realm roles (string list)
#   input.context.tenant_id  (optional) — resource tenant
#   input.context.user_id    (optional) — resource owner
#   input.context.claimed_by (optional) — for `Run.claimed_by` worker checks

package slm_forge.tenancy

import rego.v1

# same_tenant: the resource and the caller share the tenant.
# True when either side omits the field (backwards-compat).
same_tenant if {
	not input.context.tenant_id
}

same_tenant if {
	not input.user.tenant_id
}

same_tenant if {
	input.user.tenant_id == input.context.tenant_id
}

# same_owner: the resource was written by this user. True when either
# side omits the field — non-tenant-scoped endpoints stay matrix-only.
same_owner if {
	not input.context.user_id
}

same_owner if {
	input.user.id == input.context.user_id
}

# Admins in the resource's tenant see all rows in that tenant.
admin_in_tenant if {
	"admin" in input.user.roles
	same_tenant
}

# Workers are scoped to runs they have claimed. Workers MUST NOT enumerate.
worker_claim_match if {
	"worker" in input.user.roles
	input.context.claimed_by == input.user.id
}

# tenant_allow: the OWNERSHIP layer. Combine with the role matrix in
# slm_forge.allow — both must hold.
default tenant_allow := false

tenant_allow if admin_in_tenant

tenant_allow if {
	same_tenant
	same_owner
}

tenant_allow if worker_claim_match