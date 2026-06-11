/**
 * /admin/users — admin-only Keycloak user listing.
 *
 * - Gated by <RequireRole role="admin">.
 * - 501 from the backend means Keycloak admin creds aren't configured;
 *   we show an explanatory panel with a link to the Keycloak admin console.
 */
import { useEffect, useState } from 'react';
import RequireRole from '../components/RequireRole';
import { API_URL } from '../lib/api';
import { auth } from '../auth/keycloak';

type AdminUser = {
  id: string;
  email: string;
  username?: string;
  roles: string[];
  groups: string[];
  last_login: string | null;
  enabled?: boolean;
};

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ok'; users: AdminUser[] }
  | { kind: 'not-configured'; detail: string }
  | { kind: 'error'; status: number; detail: string };

export default function AdminUsersPage() {
  return (
    <RequireRole role="admin">
      <AdminUsersInner />
    </RequireRole>
  );
}

function AdminUsersInner() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = auth.getAccessToken();
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        const r = await fetch(`${API_URL}/api/v1/auth/users`, { headers });
        if (cancelled) return;
        if (r.status === 501) {
          let detail = '';
          try {
            detail = (await r.json()).detail ?? '';
          } catch {
            /* ignore */
          }
          setState({ kind: 'not-configured', detail });
          return;
        }
        if (!r.ok) {
          let detail = '';
          try {
            detail = (await r.json()).detail ?? '';
          } catch {
            /* ignore */
          }
          setState({ kind: 'error', status: r.status, detail });
          return;
        }
        const users = (await r.json()) as AdminUser[];
        setState({ kind: 'ok', users });
      } catch (e) {
        if (!cancelled)
          setState({
            kind: 'error',
            status: 0,
            detail: e instanceof Error ? e.message : String(e),
          });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
          <p className="mt-1 text-sm text-zinc-400">Keycloak-managed users with access to SLM-Forge.</p>
        </div>
        <a
          href={keycloakAdminUrl()}
          target="_blank"
          rel="noreferrer"
          className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
        >
          Open Keycloak Admin →
        </a>
      </div>

      {state.kind === 'loading' && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-6 text-sm text-zinc-400">
          Loading users…
        </div>
      )}

      {state.kind === 'not-configured' && (
        <div className="rounded-xl border border-amber-900/60 bg-amber-950/30 p-6 text-sm text-amber-100">
          <div className="mb-2 text-base font-semibold">Keycloak admin API not configured</div>
          <p className="text-amber-200/80">
            {state.detail ||
              'The backend does not have Keycloak admin credentials, so it cannot list users directly. Use the Keycloak Admin Console to manage users.'}
          </p>
          <a
            href={keycloakAdminUrl()}
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-block rounded-md bg-amber-600/80 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600"
          >
            Open Keycloak Admin Console
          </a>
        </div>
      )}

      {state.kind === 'error' && (
        <div className="rounded-xl border border-red-900/60 bg-red-950/30 p-6 text-sm text-red-100">
          <div className="mb-1 font-semibold">Failed to load users (HTTP {state.status})</div>
          <div className="font-mono text-xs text-red-200/80">{state.detail}</div>
        </div>
      )}

      {state.kind === 'ok' && (
        <div className="overflow-hidden rounded-xl border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-left text-xs uppercase tracking-wide text-zinc-400">
              <tr>
                <th className="px-4 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium">Roles</th>
                <th className="px-4 py-3 font-medium">Groups</th>
                <th className="px-4 py-3 font-medium">Last login</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {state.users.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-zinc-500">
                    No users found.
                  </td>
                </tr>
              )}
              {state.users.map((u) => (
                <tr key={u.id} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-3 text-zinc-100">{u.email || u.username || u.id}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {u.roles.map((r) => (
                        <span
                          key={r}
                          className="rounded-full border border-zinc-700 bg-zinc-800/60 px-2 py-0.5 text-xs text-zinc-300"
                        >
                          {r}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-zinc-300">
                    {u.groups.length === 0 ? <span className="text-zinc-500">—</span> : u.groups.join(', ')}
                  </td>
                  <td className="px-4 py-3 text-zinc-400">
                    {u.last_login ? new Date(u.last_login).toLocaleString() : <span className="text-zinc-500">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function keycloakAdminUrl(): string {
  const cfg = auth.getConfig();
  const base = (cfg?.keycloak_url || 'http://localhost:8080').replace(/\/$/, '');
  return `${base}/admin`;
}
