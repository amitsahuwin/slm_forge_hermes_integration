/**
 * Top-right user widget used by the Nav.
 *
 * - Auth disabled → "Local mode" pill.
 * - Auth enabled, logged out → "Sign in" button.
 * - Auth enabled, logged in → email + role pill + dropdown
 *   ("Admin → Users" if admin, "Sign out").
 */
import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export default function UserBadge() {
  const { disabled, user, login, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  if (disabled) {
    return (
      <span
        title="Authentication is disabled on the backend. Treat this session as a local admin."
        className="rounded-full border border-white/30 bg-white/10 px-2.5 py-1 text-xs font-medium text-white"
      >
        Local mode
      </span>
    );
  }

  if (!user) {
    return (
      <button
        onClick={() => login(window.location.pathname)}
        className="rounded-md border border-white/30 bg-white/10 px-3 py-1.5 text-sm font-medium text-white hover:border-white/50 hover:bg-white/20"
      >
        Sign in
      </button>
    );
  }

  const isAdmin = user.roles.includes('admin');
  const primaryRole = isAdmin ? 'admin' : user.roles[0] ?? 'user';

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-md border border-white/30 bg-white/10 px-2.5 py-1.5 text-sm text-white hover:border-white/50 hover:bg-white/20"
      >
        <span className="max-w-[16ch] truncate">{user.email}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            isAdmin
              ? 'border border-hcl-teal/50 bg-hcl-teal/20 text-white'
              : 'border border-white/30 bg-white/10 text-white/80'
          }`}
        >
          {primaryRole}
        </span>
        <svg width="10" height="10" viewBox="0 0 10 10" className="text-white/60">
          <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-56 overflow-hidden rounded-xl border border-hcl-light-blue bg-white shadow-xl">
          <div className="border-b border-hcl-light-blue px-3 py-2 text-xs text-hcl-dark/60">
            <div className="truncate text-hcl-dark">{user.email}</div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-hcl-dark/40">{user.id}</div>
          </div>
          {isAdmin && (
            <NavLink
              to="/admin/users"
              onClick={() => setOpen(false)}
              className="block px-3 py-2 text-sm text-hcl-dark hover:bg-hcl-tech-grey"
            >
              Admin → Users
            </NavLink>
          )}
          <button
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="block w-full px-3 py-2 text-left text-sm text-hcl-dark hover:bg-hcl-tech-grey"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
