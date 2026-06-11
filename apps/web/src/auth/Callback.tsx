/**
 * Route handler for /auth/callback.
 *
 * Keycloak redirects here after the user signs in. We complete the
 * authorization-code exchange via `auth.handleCallback()` and then
 * navigate to the `returnTo` path encoded in the OIDC state.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { auth } from './keycloak';
import { useAuth } from './AuthContext';

export default function Callback() {
  const navigate = useNavigate();
  const { refreshUser } = useAuth();
  const [error, setError] = useState<string | null>(null);
  // Guard against React 18 StrictMode double-mount.
  //
  // signinRedirectCallback() consumes the one-time `code` query param at
  // Keycloak's /token endpoint. If useEffect runs twice (which StrictMode
  // does on purpose in dev), the second invocation re-sends the same code
  // → Keycloak rejects it with "Code already used" → we never store a
  // token → every subsequent API request 401s. A useRef survives the
  // intentional unmount/remount StrictMode performs, so the second pass
  // is a no-op.
  const handledRef = useRef(false);

  useEffect(() => {
    if (handledRef.current) return;
    handledRef.current = true;

    (async () => {
      try {
        const dest = await auth.handleCallback();
        await refreshUser();
        navigate(dest, { replace: true });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
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
