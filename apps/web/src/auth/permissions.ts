/**
 * Client-side mirror of policies/role_matrix.rego.
 *
 * Purpose: lets us HIDE UI controls a user can't use, so they don't bump
 * into a 403 toast for every disabled action. OPA on the server remains
 * the source of truth — this is purely cosmetic. If the matrices drift,
 * the user sees a button that 403s; security is unaffected.
 *
 * Keep this file in lock-step with `policies/role_matrix.rego`. The
 * `make opa-test` policies are the canonical reference.
 */
export type Action = 'read' | 'create' | 'update' | 'delete' | 'execute' | 'export';
export type Resource =
  | 'dataset'
  | 'experiment'
  | 'run'
  | 'export'
  | 'log'
  | 'setting'
  | 'research'
  | 'chat';

type Matrix = Record<string, Partial<Record<Resource, ReadonlySet<Action>>>>;

const ROLE_MATRIX: Matrix = {
  admin: {
    dataset:    new Set(['read', 'create', 'update', 'delete']),
    experiment: new Set(['read', 'create', 'update', 'delete']),
    run:        new Set(['read', 'create', 'update', 'delete', 'execute', 'export']),
    export:     new Set(['read', 'create', 'update', 'delete', 'execute']),
    log:        new Set(['read', 'create', 'update', 'delete']),
    setting:    new Set(['read', 'create', 'update', 'delete']),
    research:   new Set(['read', 'create', 'update', 'delete']),
    chat:       new Set(['read', 'create', 'update', 'delete']),
  },
  data_engineer: {
    dataset:    new Set(['read', 'create', 'update', 'delete']),
    experiment: new Set(['read', 'create', 'update', 'delete']),
    run:        new Set(['read', 'update']),
    export:     new Set(['read', 'execute']),
    log:        new Set(['read']),
    research:   new Set(['read']),
    chat:       new Set(['read', 'create', 'update']),
  },
  domain_expert: {
    dataset:    new Set(['read', 'update']),
    experiment: new Set(['read']),
    run:        new Set(['read']),
    export:     new Set(['read']),
    research:   new Set(['read', 'create', 'update', 'delete']),
    chat:       new Set(['read', 'create', 'update']),
  },
  devops: {
    run:      new Set(['read']),
    log:      new Set(['read', 'create', 'update', 'delete']),
    setting:  new Set(['read', 'create', 'update', 'delete']),
    research: new Set(['read']),
    chat:     new Set(['read']),
  },
  operations: {
    dataset:    new Set(['read']),
    experiment: new Set(['read']),
    run:        new Set(['read']),
    export:     new Set(['read', 'execute']),
    log:        new Set(['read']),
    research:   new Set(['read']),
    chat:       new Set(['read']),
  },
  support: {
    dataset:    new Set(['read']),
    experiment: new Set(['read']),
    run:        new Set(['read']),
    export:     new Set(['read']),
    log:        new Set(['read']),
    setting:    new Set(['read']),
    research:   new Set(['read']),
    chat:       new Set(['read']),
  },
};

/**
 * Returns true if any of the user's roles grants ``action`` on ``resource``.
 * Admin and the service-account role always bypass.
 */
export function can(roles: readonly string[], action: Action, resource: Resource): boolean {
  if (!roles || roles.length === 0) return false;
  if (roles.includes('admin') || roles.includes('service')) return true;
  for (const r of roles) {
    const perResource = ROLE_MATRIX[r];
    if (!perResource) continue;
    const allowed = perResource[resource];
    if (allowed && allowed.has(action)) return true;
  }
  return false;
}

/**
 * The same matrix exposed as a tabs predicate. Pages are top-level nav
 * destinations; their "readability" maps to (read, <resource>):
 *
 *   Datasets    → read dataset
 *   Experiments → read experiment
 *   Runs        → read run
 *   Exports     → read export
 *   Maintenance → read setting
 *   R&D         → read research
 *   Chat        → read chat
 *   Agents      → read chat (agents share the chat permission)
 *
 * Dashboard is always visible (just shows status).
 */
export type NavKey =
  | 'dashboard'
  | 'experiments'
  | 'runs'
  | 'exports'
  | 'datasets'
  | 'maintenance'
  | 'chat'
  | 'research'
  | 'agents'
  | 'traces'
  | 'autofix'
  | 'admin';

const NAV_TO_PERM: Record<NavKey, { action: Action; resource: Resource } | 'always' | 'admin_only'> = {
  dashboard: 'always',
  experiments: { action: 'read', resource: 'experiment' },
  runs: { action: 'read', resource: 'run' },
  exports: { action: 'read', resource: 'export' },
  datasets: { action: 'read', resource: 'dataset' },
  maintenance: { action: 'read', resource: 'setting' },
  chat: { action: 'read', resource: 'chat' },
  research: { action: 'read', resource: 'research' },
  agents: { action: 'read', resource: 'chat' },
  // The Hermes traces tab exposes raw prompt + response bodies; admin only.
  traces: 'admin_only',
  // PR-C — auto-fix attempts may carry stack traces / source paths; admin only.
  autofix: 'admin_only',
  admin: 'admin_only',
};

export function canSeeNav(roles: readonly string[], key: NavKey): boolean {
  const spec = NAV_TO_PERM[key];
  if (spec === 'always') return true;
  if (spec === 'admin_only') return roles.includes('admin');
  return can(roles, spec.action, spec.resource);
}
