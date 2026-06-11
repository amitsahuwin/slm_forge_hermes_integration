# Role → action → resource matrix (Phase M.3).
#
# Declarative source of truth for what each role may do. The main policy
# (`slm_forge.rego`) joins user roles against this map.
#
# Action vocabulary: read | create | update | delete | execute | export
# Resource vocabulary: dataset | experiment | run | export | log
#                      | setting | research | chat
#
# `update_readme` is a sub-flavor of `update` we only grant to domain
# experts on datasets — see PLAN.md Phase M.3.

package slm_forge.matrix

# admin → CRUD on every resource. Encoded as the wildcard "*" entry that
# the main policy expands when it sees it.
matrix["admin"] = {
	"dataset":    {"read", "create", "update", "delete"},
	"experiment": {"read", "create", "update", "delete"},
	"run":        {"read", "create", "update", "delete", "execute", "export"},
	"export":     {"read", "create", "update", "delete", "execute"},
	"log":        {"read", "create", "update", "delete"},
	"setting":    {"read", "create", "update", "delete"},
	"research":   {"read", "create", "update", "delete"},
	"chat":       {"read", "create", "update", "delete"},
}

# data_engineer → datasets:CRUD, experiments:CRUD, runs:R+cancel,
# exports:execute, logs:R, research:R, chat:RW.
# "cancel" maps onto `update` (the runs API uses PATCH to flip status).
matrix["data_engineer"] = {
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
# "update_readme" rolls into `update` at the policy layer — the matrix is
# coarse-grained and the rego rule decides which `update` calls are
# allowed (see slm_forge.rego if we ever need finer cuts).
matrix["domain_expert"] = {
	"dataset":    {"read", "update"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read"},
	"research":   {"read", "create", "update", "delete"},
	"chat":       {"read", "create", "update"},
}

# devops → runs:R, logs:RW, settings:RW, research:R, chat:R.
matrix["devops"] = {
	"run":      {"read"},
	"log":      {"read", "create", "update", "delete"},
	"setting":  {"read", "create", "update", "delete"},
	"research": {"read"},
	"chat":     {"read"},
}

# operations → datasets:R, experiments:R, runs:R, exports:execute,
# logs:R, research:R, chat:R.
matrix["operations"] = {
	"dataset":    {"read"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read", "execute"},
	"log":        {"read"},
	"research":   {"read"},
	"chat":       {"read"},
}

# support → everything:R.
matrix["support"] = {
	"dataset":    {"read"},
	"experiment": {"read"},
	"run":        {"read"},
	"export":     {"read"},
	"log":        {"read"},
	"setting":    {"read"},
	"research":   {"read"},
	"chat":       {"read"},
}
