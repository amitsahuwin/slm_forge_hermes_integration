/**
 * Gate children behind a role. When auth is disabled, always renders children
 * (the synthetic admin has the `admin` role anyway, but we short-circuit for
 * clarity and to avoid one extra check).
 */
import type { ReactNode } from 'react';
import { useAuth } from '../auth/AuthContext';

export default function RequireRole({
  role,
  children,
}: {
  role: string;
  children: ReactNode;
}) {
  const { disabled, user, login } = useAuth();

  if (disabled) return <>{children}</>;

  if (!user) {
    return (
      <div className="mx-auto max-w-md rounded-xl border border-hcl-light-blue bg-white p-6 text-sm text-hcl-dark/80">
        <div className="mb-2 text-base font-semibold text-hcl-dark">Sign in required</div>
        <p className="text-hcl-dark/60">You need to be signed in to view this page.</p>
        <button
          onClick={() => login(window.location.pathname)}
          className="mt-4 rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
        >
          Sign in
        </button>
      </div>
    );
  }

  if (!user.roles.includes(role)) {
    return (
      <div className="mx-auto max-w-md rounded-xl border border-hcl-warning/30 bg-hcl-warning/10 p-6 text-sm text-hcl-warning">
        <div className="mb-2 text-base font-semibold">You don't have permission</div>
        <p className="text-hcl-warning/80">
          This page requires the <code className="rounded bg-hcl-warning/10 px-1 py-0.5">{role}</code> role. Ask your
          administrator to grant access.
        </p>
        <div className="mt-3 text-xs text-hcl-warning/60">
          Signed in as <span className="font-mono">{user.email}</span>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
