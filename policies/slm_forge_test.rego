# OPA unit tests for slm_forge policy.
#
# Run with:
#   opa test policies/
#
# Covers the role matrix (M.3) plus the negative-path cases that bit us
# during planning: empty roles, unknown roles, admin override.

package slm_forge

# ─── Admin override ─────────────────────────────────────────────────────────

test_admin_can_delete_dataset {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "delete",
		"resource": "dataset",
	}
}

test_admin_can_execute_export {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

# ─── data_engineer happy paths ──────────────────────────────────────────────

test_data_engineer_can_create_dataset {
	allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

test_data_engineer_can_execute_export {
	allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

# ─── data_engineer denials ──────────────────────────────────────────────────

test_data_engineer_cannot_delete_export {
	not allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "delete",
		"resource": "export",
	}
}

test_data_engineer_cannot_update_settings {
	not allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "update",
		"resource": "setting",
	}
}

# ─── domain_expert: read-mostly with research RW ────────────────────────────

test_domain_expert_can_create_research {
	allow with input as {
		"user":     {"id": "carol", "roles": ["domain_expert"], "groups": []},
		"action":   "create",
		"resource": "research",
	}
}

test_domain_expert_cannot_create_run {
	not allow with input as {
		"user":     {"id": "carol", "roles": ["domain_expert"], "groups": []},
		"action":   "create",
		"resource": "run",
	}
}

# ─── devops: settings RW, no datasets ───────────────────────────────────────

test_devops_can_update_setting {
	allow with input as {
		"user":     {"id": "dave", "roles": ["devops"], "groups": []},
		"action":   "update",
		"resource": "setting",
	}
}

test_devops_cannot_read_dataset {
	not allow with input as {
		"user":     {"id": "dave", "roles": ["devops"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

# ─── operations: execute exports ────────────────────────────────────────────

test_operations_can_execute_export {
	allow with input as {
		"user":     {"id": "eve", "roles": ["operations"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

test_operations_cannot_create_dataset {
	not allow with input as {
		"user":     {"id": "eve", "roles": ["operations"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

# ─── support: read-only ─────────────────────────────────────────────────────

test_support_can_read_dataset {
	allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

test_support_cannot_create_anything {
	not allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

test_support_cannot_delete_run {
	not allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "delete",
		"resource": "run",
	}
}

# ─── Edge cases: unknown / empty roles ──────────────────────────────────────

test_unknown_role_denied {
	not allow with input as {
		"user":     {"id": "mallory", "roles": ["intern"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

test_empty_roles_denied {
	not allow with input as {
		"user":     {"id": "ghost", "roles": [], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

# ─── Multi-role union ───────────────────────────────────────────────────────
# A user with multiple roles gets the union of their permissions.

test_multi_role_union_allows {
	allow with input as {
		"user":     {"id": "multi", "roles": ["support", "data_engineer"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}
