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

import data.slm_forge.matrix

default allow = false

# Admins bypass the matrix entirely.
allow {
	is_admin
}

is_admin {
	some r
	r := input.user.roles[_]
	r == "admin"
}

# Otherwise: any role the user has must permit the (action, resource) pair.
allow {
	some role
	role := input.user.roles[_]
	permitted(role, input.action, input.resource)
}

# Helper: role `role` has permission for `action` on `resource`?
permitted(role, action, resource) {
	allowed_actions := matrix.matrix[role][resource]
	allowed_actions[action]
}

# ─── Reason (human-readable denial explanation) ─────────────────────────────
#
# The FastAPI side surfaces this string as the 403 detail. We compute three
# possible reasons in priority order:
#   1. No roles at all → "no roles assigned".
#   2. Unknown role(s) → list them.
#   3. Default       → "role(s) X lack <action> on <resource>".

default reason = ""

reason = msg {
	not allow
	count(input.user.roles) == 0
	msg := "no roles assigned"
}

reason = msg {
	not allow
	count(input.user.roles) > 0
	# All roles unknown to the matrix → tell the user.
	unknown := [r | r := input.user.roles[_]; not matrix.matrix[r]]
	count(unknown) == count(input.user.roles)
	msg := sprintf("unknown role(s): %v", [unknown])
}

reason = msg {
	not allow
	count(input.user.roles) > 0
	# At least one role is known but none permit the requested action.
	known := [r | r := input.user.roles[_]; matrix.matrix[r]]
	count(known) > 0
	msg := sprintf(
		"role(s) %v lack '%v' on '%v'",
		[known, input.action, input.resource],
	)
}
