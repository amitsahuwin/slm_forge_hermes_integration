# Role → action → resource matrix (Phase M.3).
#
# Declarative source of truth for what each role may do. The main policy
# (`slm_forge.rego`) joins user roles against this map.
#
# Action vocabulary: read | create | update | delete | execute | export
# Resource vocabulary: dataset | experiment | run | export | log
#                      | setting | research | chat | model
#
# `update_readme` is a sub-flavor of `update` we only grant to domain
# experts on datasets — see PLAN.md Phase M.3.

package slm_forge.matrix

import rego.v1

# admin → CRUD on every resource.
matrix["admin"] := {
	"dataset":    {"read", "create", "update", "delete"},
	"experiment": {"read", "create", "update", "delete"},
	"run":        {"read", "create", "update", "delete", "execute", "export"},
	"export":     {"read", "create", "update", "delete", "execute"},
	"log":        {"read", "create", "update", "delete"},
	"setting":    {"read", "create", "update", "delete"},
	"research":   {"read", "create", "update", "delete"},
	"chat":       {"read", "create", "update", "delete"},
	# Dynamic model registry (Models tab) is global; only admins may
	# register (create) or remove (delete). Listing is an open read.
	"model":      {"read", "create", "update", "delete"},
}

# data_engineer → datasets:CRUD, experiments:CRUD, runs:R+cancel,
# exports:execute, logs:R, research:R, chat:RW.
# "cancel" maps onto `update` (the runs API uses PATCH to flip status).
matrix["data_engineer"] := {
	"dataset":    {"read", "create", "update", "delete"},
	"experiment": {"read", "create", "update", "delete"},
	"run":        {"read", "update"},
	"export":     {"read", "execute"},
	"log":        {"read"},
	"research":   {"read"},
	"chat":       {"read", "create", "update"},
}

# domain_expert → datasets:R+update_readme, experiments:R, runs:R,
# exports:R, research:RW, chat:RW.
matrix["domain_expert"] := {
	"dataset":    {"read", "update"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read"},
	"research":   {"read", "create", "update", "delete"},
	"chat":       {"read", "create", "update"},
}

# devops → runs:R, logs:RW, settings:RW, research:R, chat:R.
matrix["devops"] := {
	"run":      {"read"},
	"log":      {"read", "create", "update", "delete"},
	"setting":  {"read", "create", "update", "delete"},
	"research": {"read"},
	"chat":     {"read"},
}

# operations → datasets:R, experiments:R, runs:R, exports:execute,
# logs:R, research:R, chat:R.
matrix["operations"] := {
	"dataset":    {"read"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read", "execute"},
	"log":        {"read"},
	"research":   {"read"},
	"chat":       {"read"},
}

# support → everything:R.
matrix["support"] := {
	"dataset":    {"read"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read"},
	"log":        {"read"},
	"setting":    {"read"},
	"research":   {"read"},
	"chat":       {"read"},
}

# Phase C — viewer is the read-only baseline used for users without a
# stronger role. Mirrors support but explicit so the matrix doesn't
# silently inherit from another role.
matrix["viewer"] := {
	"dataset":    {"read"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read"},
	"log":        {"read"},
	"research":   {"read"},
	"chat":       {"read"},
}

# Phase C — worker (service account for trainer / ratchet / exporter).
# Narrowly scoped to the claim-and-upload path; workers must never list
# arbitrary runs or read other tenants' resources. Cross-tenant access
# is permitted *only* through ``tenancy.worker_claim_match`` on rows
# the worker has actually claimed.
matrix["worker"] := {
	"dataset":    {"read"},
	"run":        {"read", "update"},
	"export":     {"read", "execute"},
	"log":        {"create"},
}
