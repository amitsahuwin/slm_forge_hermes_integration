/**
 * React context that wraps the `auth` singleton.
 *
 * - On mount, calls `auth.init()` once.
 * - While init is pending, renders a minimal loading splash so children
 *   never observe a half-initialized auth state.
 * - Exposes `{disabled, user, login, logout, refreshUser}` via `useAuth()`.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { auth, type AppUser } from './keycloak';

type AuthContextValue = {
  disabled: boolean;
  user: AppUser | null;
  login: (returnTo?: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<AppUser | null>(null);

  useEffect(() => {
    let cancelled = false;
    auth.init().then(() => {
      if (cancelled) return;
      setUser(auth.getUser());
      setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const value: AuthContextValue = {
    disabled: auth.disabled,
    user,
    login: async (returnTo?: string) => {
      await auth.login(returnTo);
    },
    logout: async () => {
      await auth.logout();
    },
    refreshUser: async () => {
      await auth.refreshUser();
      setUser(auth.getUser());
    },
  };

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        <div className="flex items-center gap-3 text-sm">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-700 border-t-zinc-300" />
          <span>Initializing…</span>
        </div>
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth() must be used inside <AuthProvider>');
  return ctx;
}
