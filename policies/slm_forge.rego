# Main authorization policy (Phase M.2).
#
# Input shape (POSTed from the FastAPI middleware):
#   {
#     "input": {
#       "user": {"id": "alice", "roles": ["data_engineer"], "groups": []},
#       "action": "delete",
#       "resource": "run"
#     }
#   }
#
# Output shape on `/v1/data/slm_forge/allow`:
#   {"result": true}    -- when the decision is to allow
#   {"result": false}   -- when the decision is to deny
#
# The middleware also queries `/v1/data/slm_forge/reason` for the
# human-readable denial message used in the 403 response.

package slm_forge

# Opt into the OPA 1.0+ strict / future-keyword syntax. Lets us use
# `if`, `in`, `contains`, etc. without per-line imports.
import rego.v1

import data.slm_forge.matrix

default allow := false

# Admins bypass the matrix entirely.
allow if is_admin

is_admin if "admin" in input.user.roles

# Otherwise: any role the user has must permit the (action, resource) pair.
allow if {
	some role in input.user.roles
	permitted(role, input.action, input.resource)
}

# Helper: role `role` has permission for `action` on `resource`?
permitted(role, action, resource) if {
	allowed_actions := matrix.matrix[role][resource]
	allowed_actions[action]
}

# ─── Reason (human-readable denial explanation) ─────────────────────────────
#
# The FastAPI side surfaces this string as the 403 detail. We compute three
# possible reasons in priority order:
#   1. No roles at all → "no roles assigned".
#   2. All roles unknown → list them.
#   3. Default → "role(s) X lack <action> on <resource>".

default reason := ""

reason := msg if {
	not allow
	count(input.user.roles) == 0
	msg := "no roles assigned"
}

reason := msg if {
	not allow
	count(input.user.roles) > 0
	unknown := [r | some r in input.user.roles; not matrix.matrix[r]]
	count(unknown) == count(input.user.roles)
	msg := sprintf("unknown role(s): %v", [unknown])
}

reason := msg if {
	not allow
	count(input.user.roles) > 0
	known := [r | some r in input.user.roles; matrix.matrix[r]]
	count(known) > 0
	msg := sprintf(
		"role(s) %v lack '%v' on '%v'",
		[known, input.action, input.resource],
	)
}
