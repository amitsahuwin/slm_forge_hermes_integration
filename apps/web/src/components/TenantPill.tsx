import { useAuth } from '../auth/AuthContext';

// Phase C — shows the resolved tenant + primary role of the signed-in
// user. When auth is disabled (dev mode) the synthetic admin yields
// `tenant_id="local"`, `primary_role="admin"`. When auth is on but the
// user has no Keycloak tenant group, we surface a warning pill so the
// operator notices the configuration gap before they hit a 403.
export default function TenantPill() {
  const { user, disabled } = useAuth();
  if (!user) return null;

  const tenant = user.tenant_id || '';
  const role = user.primary_role || (user.roles?.[0] ?? '');

  if (!tenant) {
    return (
      <span
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-700 bg-amber-950/40 px-2 py-1 text-[11px] font-medium text-amber-200"
        title="No Keycloak group like /tenants/<name> assigned. Set one in the realm and re-login."
      >
        ⚠ no tenant
      </span>
    );
  }

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-[11px] font-medium text-zinc-300"
      title={
        disabled
          ? 'Auth is disabled — synthetic admin identity'
          : `Tenant: ${tenant} · Role: ${role}`
      }
    >
      <span className="text-emerald-300">{tenant}</span>
      <span className="text-zinc-600">·</span>
      <span className="text-zinc-400">{role}</span>
    </span>
  );
}