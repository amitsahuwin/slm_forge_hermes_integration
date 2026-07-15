# OPA unit tests for slm_forge policy.
#
# Run with:
#   opa test policies/
# or:
#   make opa-test

package slm_forge

import rego.v1

# ─── Admin override ─────────────────────────────────────────────────────────

test_admin_can_delete_dataset if {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "delete",
		"resource": "dataset",
	}
}

test_admin_can_execute_export if {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

# ─── data_engineer happy paths ──────────────────────────────────────────────

test_data_engineer_can_create_dataset if {
	allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

test_data_engineer_can_execute_export if {
	allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

# ─── data_engineer denials ──────────────────────────────────────────────────

test_data_engineer_cannot_delete_export if {
	not allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "delete",
		"resource": "export",
	}
}

test_data_engineer_cannot_update_settings if {
	not allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "update",
		"resource": "setting",
	}
}

# ─── domain_expert: read-mostly with research RW ────────────────────────────

test_domain_expert_can_create_research if {
	allow with input as {
		"user":     {"id": "carol", "roles": ["domain_expert"], "groups": []},
		"action":   "create",
		"resource": "research",
	}
}

test_domain_expert_cannot_create_run if {
	not allow with input as {
		"user":     {"id": "carol", "roles": ["domain_expert"], "groups": []},
		"action":   "create",
		"resource": "run",
	}
}

# ─── devops: settings RW, no datasets ───────────────────────────────────────

test_devops_can_update_setting if {
	allow with input as {
		"user":     {"id": "dave", "roles": ["devops"], "groups": []},
		"action":   "update",
		"resource": "setting",
	}
}

test_devops_cannot_read_dataset if {
	not allow with input as {
		"user":     {"id": "dave", "roles": ["devops"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

# ─── operations: execute exports ────────────────────────────────────────────

test_operations_can_execute_export if {
	allow with input as {
		"user":     {"id": "eve", "roles": ["operations"], "groups": []},
		"action":   "execute",
		"resource": "export",
	}
}

test_operations_cannot_create_dataset if {
	not allow with input as {
		"user":     {"id": "eve", "roles": ["operations"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

# ─── support: read-only ─────────────────────────────────────────────────────

test_support_can_read_dataset if {
	allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

test_support_cannot_create_anything if {
	not allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

test_support_cannot_delete_run if {
	not allow with input as {
		"user":     {"id": "frank", "roles": ["support"], "groups": []},
		"action":   "delete",
		"resource": "run",
	}
}

# ─── Edge cases: unknown / empty roles ──────────────────────────────────────

test_unknown_role_denied if {
	not allow with input as {
		"user":     {"id": "mallory", "roles": ["intern"], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

test_empty_roles_denied if {
	not allow with input as {
		"user":     {"id": "ghost", "roles": [], "groups": []},
		"action":   "read",
		"resource": "dataset",
	}
}

# ─── Multi-role union ───────────────────────────────────────────────────────
# A user with multiple roles gets the union of their permissions.

test_multi_role_union_allows if {
	allow with input as {
		"user":     {"id": "multi", "roles": ["support", "data_engineer"], "groups": []},
		"action":   "create",
		"resource": "dataset",
	}
}

# ─── Phase C — tenant isolation matrix ──────────────────────────────────────

test_same_tenant_same_user_can_read_own_run if {
	allow with input as {
		"user":     {"id": "alice", "tenant_id": "acme", "roles": ["data_engineer"], "groups": ["/tenants/acme"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice"},
	}
}

test_same_tenant_other_user_non_admin_denied if {
	not allow with input as {
		"user":     {"id": "bob",   "tenant_id": "acme", "roles": ["data_engineer"], "groups": ["/tenants/acme"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice"},
	}
}

test_admin_can_read_any_user_in_their_tenant if {
	allow with input as {
		"user":     {"id": "admin", "tenant_id": "acme", "roles": ["admin"], "groups": ["/tenants/acme"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice"},
	}
}

test_admin_cannot_cross_tenant if {
	not allow with input as {
		"user":     {"id": "admin", "tenant_id": "acme", "roles": ["admin"], "groups": ["/tenants/acme"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "globex", "user_id": "carol"},
	}
}

test_other_tenant_any_role_denied if {
	not allow with input as {
		"user":     {"id": "carol", "tenant_id": "globex", "roles": ["data_engineer"], "groups": ["/tenants/globex"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice"},
	}
}

# ─── Worker scope ───────────────────────────────────────────────────────────

test_worker_can_update_claimed_run if {
	allow with input as {
		"user":     {"id": "trainer-bot", "tenant_id": "system", "roles": ["worker"], "groups": ["/tenants/system"]},
		"action":   "update",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice", "claimed_by": "trainer-bot"},
	}
}

test_worker_cannot_update_unclaimed_run if {
	not allow with input as {
		"user":     {"id": "trainer-bot", "tenant_id": "system", "roles": ["worker"], "groups": ["/tenants/system"]},
		"action":   "update",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice", "claimed_by": "other-bot"},
	}
}

test_worker_can_read_dataset_for_claimed_run if {
	allow with input as {
		"user":     {"id": "trainer-bot", "tenant_id": "system", "roles": ["worker"], "groups": ["/tenants/system"]},
		"action":   "read",
		"resource": "dataset",
		"context":  {"tenant_id": "acme", "user_id": "alice", "claimed_by": "trainer-bot"},
	}
}

test_worker_cannot_delete_anything if {
	not allow with input as {
		"user":     {"id": "trainer-bot", "tenant_id": "system", "roles": ["worker"], "groups": ["/tenants/system"]},
		"action":   "delete",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "alice", "claimed_by": "trainer-bot"},
	}
}

# ─── Viewer scope ───────────────────────────────────────────────────────────

test_viewer_can_read_own_tenant_run if {
	allow with input as {
		"user":     {"id": "v1", "tenant_id": "acme", "roles": ["viewer"], "groups": ["/tenants/acme"]},
		"action":   "read",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "v1"},
	}
}

test_viewer_cannot_create_run if {
	not allow with input as {
		"user":     {"id": "v1", "tenant_id": "acme", "roles": ["viewer"], "groups": ["/tenants/acme"]},
		"action":   "create",
		"resource": "run",
		"context":  {"tenant_id": "acme", "user_id": "v1"},
	}
}

# ─── Dynamic model registry (Models tab) ────────────────────────────────────

test_admin_can_create_model if {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "create",
		"resource": "model",
	}
}

test_admin_can_delete_model if {
	allow with input as {
		"user":     {"id": "alice", "roles": ["admin"], "groups": []},
		"action":   "delete",
		"resource": "model",
	}
}

test_data_engineer_cannot_create_model if {
	not allow with input as {
		"user":     {"id": "bob", "roles": ["data_engineer"], "groups": []},
		"action":   "create",
		"resource": "model",
	}
}

test_viewer_cannot_delete_model if {
	not allow with input as {
		"user":     {"id": "v1", "roles": ["viewer"], "groups": []},
		"action":   "delete",
		"resource": "model",
	}
}
