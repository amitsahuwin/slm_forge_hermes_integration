/**
 * Global window.fetch interceptor.
 *
 * Replaces window.fetch with a wrapper that, for every request to the
 * SLM-Forge API origin, injects `Authorization: Bearer <token>` automatically.
 * Eliminates the per-call-site `authFetch` wiring — page code can keep using
 * plain `fetch()` and it just works.
 *
 * Also handles 401 by redirecting to Keycloak when auth is enabled.
 *
 * Must be installed once, BEFORE any module imports something that calls
 * fetch at module-eval time (see main.tsx).
 */
import { auth } from './keycloak';

// Same default the rest of the app uses. We can't import API_URL from
// `lib/api.ts` because that module pulls in the toast system + other state;
// we want the interceptor to be standalone so it loads before anything else.
const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

const PATCH_FLAG = '__slmForgeFetchPatched__';

export function installFetchInterceptor(): void {
  // Idempotent. Vite HMR re-evaluates this module on hot reload.
  if ((window as unknown as Record<string, unknown>)[PATCH_FLAG]) return;
  (window as unknown as Record<string, unknown>)[PATCH_FLAG] = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = resolveUrl(input);
    const isApiCall = url.startsWith(API_URL);

    if (isApiCall) {
      // Don't clobber an Authorization header the caller deliberately set
      // (e.g. the OIDC client during code exchange uses its own scheme).
      const incomingHeaders = new Headers(init.headers ?? {});
      if (!incomingHeaders.has('Authorization')) {
        const token = auth.getAccessToken();
        if (token) {
          incomingHeaders.set('Authorization', `Bearer ${token}`);
          init = { ...init, headers: incomingHeaders };
        }
      }
    }

    const res = await originalFetch(input, init);

    // 401 from the API while auth is enabled → token expired / missing.
    // Redirect to Keycloak to re-authenticate, preserving the current path.
    if (
      isApiCall &&
      res.status === 401 &&
      !auth.disabled &&
      // Don't loop on /auth/* endpoints — those are part of the flow.
      !url.includes('/api/v1/auth/')
    ) {
      // eslint-disable-next-line no-console
      console.warn('[auth] API returned 401; redirecting to login:', url);
      // Fire-and-forget; the navigation supersedes any pending work.
      void auth.login(window.location.pathname + window.location.search);
    }

    return res;
  };
}

function resolveUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

/**
 * Helper for SSE URLs. `EventSource` cannot carry custom headers, so we
 * have to pass the token as a query param. The API middleware accepts
 * `access_token=<jwt>` as an Authorization-header equivalent.
 *
 * Usage:
 *   const es = new EventSource(withAuth(`${API_URL}/api/v1/runs/5/stream`));
 */
export function withAuth(url: string): string {
  if (auth.disabled) return url;
  const token = auth.getAccessToken();
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}access_token=${encodeURIComponent(token)}`;
}
