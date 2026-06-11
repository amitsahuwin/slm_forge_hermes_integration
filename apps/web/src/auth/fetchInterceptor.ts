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

// Lightweight toast bridge — the interceptor runs before any UI module so
// we can't import the toast system directly without creating a circular
// import. Instead we late-bind: the toast system calls `setForbiddenToastSink`
// once it's mounted, and we route 403 detail strings there. If nothing is
// bound yet, we fall back to console.warn.
let forbiddenToastSink: (msg: string) => void = (msg) => {
  // eslint-disable-next-line no-console
  console.warn('[auth] 403:', msg);
};
export function setForbiddenToastSink(fn: (msg: string) => void): void {
  forbiddenToastSink = fn;
}
function showForbiddenToast(msg: string): void {
  forbiddenToastSink(msg);
}

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

    if (isApiCall && !auth.disabled && !url.includes('/api/v1/auth/')) {
      // 401 → not signed in / session expired. Redirect to Keycloak.
      if (res.status === 401) {
        // eslint-disable-next-line no-console
        console.warn('[auth] API returned 401; redirecting to login:', url);
        void auth.login(window.location.pathname + window.location.search);
      }
      // 403 → forbidden by OPA policy. Surface the human-readable reason as
      // a toast so the user understands which role would be sufficient.
      // We clone the response so the caller can still read the body.
      if (res.status === 403) {
        try {
          const j = (await res.clone().json()) as {
            detail?: string;
            code?: string;
          };
          const detail = j.detail || 'You don\'t have permission to do this.';
          void showForbiddenToast(detail);
        } catch {
          /* non-JSON 403 — silent */
        }
      }
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
