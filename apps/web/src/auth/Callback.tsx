/**
 * Route handler for /auth/callback.
 *
 * Keycloak redirects here after the user signs in. We complete the
 * authorization-code exchange via `auth.handleCallback()` and then
 * navigate to the `returnTo` path encoded in the OIDC state.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auth } from './keycloak';
import { useAuth } from './AuthContext';

export default function Callback() {
  const navigate = useNavigate();
  const { refreshUser } = useAuth();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const dest = await auth.handleCallback();
        await refreshUser();
        if (!cancelled) navigate(dest, { replace: true });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // refreshUser identity is stable (closure over setUser); intentional one-shot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      {error ? (
        <div className="max-w-md rounded-xl border border-red-900/60 bg-red-950/30 p-6 text-sm text-red-200">
          <div className="mb-2 font-semibold">Sign-in failed</div>
          <div className="font-mono text-xs text-red-300/80">{error}</div>
          <button
            onClick={() => navigate('/', { replace: true })}
            className="mt-4 rounded-md border border-red-800 bg-red-900/40 px-3 py-1.5 text-xs hover:bg-red-900/60"
          >
            Back to home
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-3 text-sm text-zinc-400">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-700 border-t-zinc-300" />
          <span>Signing in…</span>
        </div>
      )}
    </div>
  );
}
