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
        className="rounded-full border border-zinc-700 bg-zinc-800/60 px-2.5 py-1 text-xs font-medium text-zinc-300"
      >
        Local mode
      </span>
    );
  }

  if (!user) {
    return (
      <button
        onClick={() => login(window.location.pathname)}
        className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
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
        className="flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-200 hover:border-zinc-700 hover:bg-zinc-800"
      >
        <span className="max-w-[16ch] truncate">{user.email}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            isAdmin
              ? 'border border-emerald-800 bg-emerald-950/60 text-emerald-300'
              : 'border border-zinc-700 bg-zinc-800/60 text-zinc-300'
          }`}
        >
          {primaryRole}
        </span>
        <svg width="10" height="10" viewBox="0 0 10 10" className="text-zinc-500">
          <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-56 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 shadow-xl">
          <div className="border-b border-zinc-800 px-3 py-2 text-xs text-zinc-400">
            <div className="truncate text-zinc-200">{user.email}</div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-zinc-500">{user.id}</div>
          </div>
          {isAdmin && (
            <NavLink
              to="/admin/users"
              onClick={() => setOpen(false)}
              className="block px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-900"
            >
              Admin → Users
            </NavLink>
          )}
          <button
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="block w-full px-3 py-2 text-left text-sm text-zinc-200 hover:bg-zinc-900"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
