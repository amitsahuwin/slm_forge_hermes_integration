/**
 * Phase M.5 — Keycloak / OIDC client wrapper.
 *
 * Singleton `auth` exposes a tiny, app-friendly facade over `oidc-client-ts`:
 *   - init()             — fetch /api/v1/auth/config, decide disabled vs. enabled.
 *   - login() / logout() — redirect flows.
 *   - handleCallback()   — completes the auth code redirect.
 *   - getAccessToken()   — returns the current Bearer token (or null).
 *   - getUser()          — returns the parsed app User (id/email/roles/groups).
 *
 * When `auth_enabled=false` the client never instantiates a UserManager;
 * it returns a synthetic admin so the rest of the app can stay agnostic.
 */
import { UserManager, WebStorageStateStore, type User as OidcUser } from 'oidc-client-ts';

export type AppUser = {
  id: string;
  email: string;
  roles: string[];
  groups: string[];
  // Phase C — resolved Identity fields. `tenant_id` may be empty when
  // the user has no tenant group (a configuration error post-cutover);
  // the UI shows a "no tenant assigned" message instead of crashing.
  tenant_id: string;
  primary_role: string;
  is_admin: boolean;
  is_worker: boolean;
};

export type AuthConfig = {
  auth_enabled: boolean;
  keycloak_url: string;
  realm: string;
  web_client_id: string;
};

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

const SYNTHETIC_ADMIN: AppUser = {
  id: 'local-admin',
  email: 'local-admin@slm-forge.local',
  roles: ['admin'],
  groups: ['/tenants/local'],
  tenant_id: 'local',
  primary_role: 'admin',
  is_admin: true,
  is_worker: false,
};

class AuthClient {
  private config: AuthConfig | null = null;
  private mgr: UserManager | null = null;
  private oidcUser: OidcUser | null = null;
  private appUser: AppUser | null = null;
  private initialized = false;
  private initPromise: Promise<void> | null = null;

  /** True when the backend reports `auth_enabled=false`. */
  get disabled(): boolean {
    return this.config?.auth_enabled === false;
  }

  /** Idempotent: subsequent calls return the same promise. */
  init(): Promise<void> {
    if (this.initPromise) return this.initPromise;
    this.initPromise = this._init();
    return this.initPromise;
  }

  private async _init(): Promise<void> {
    try {
      const r = await fetch(`${API_URL}/api/v1/auth/config`);
      if (!r.ok) throw new Error(`auth/config HTTP ${r.status}`);
      this.config = (await r.json()) as AuthConfig;
    } catch (e) {
      // Backend not reachable or endpoint missing — fall back to disabled mode.
      // The app still renders; API calls will just fail individually.
      // eslint-disable-next-line no-console
      console.warn('[auth] /auth/config unreachable, falling back to disabled mode:', e);
      this.config = {
        auth_enabled: false,
        keycloak_url: '',
        realm: '',
        web_client_id: '',
      };
    }

    if (!this.config.auth_enabled) {
      this.appUser = SYNTHETIC_ADMIN;
      this.initialized = true;
      return;
    }

    const authority = `${this.config.keycloak_url.replace(/\/$/, '')}/realms/${this.config.realm}`;
    this.mgr = new UserManager({
      authority,
      client_id: this.config.web_client_id,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code',
      scope: 'openid profile email',
      automaticSilentRenew: true,
      loadUserInfo: true,
      userStore: new WebStorageStateStore({ store: window.localStorage }),
    });

    // Token refresh side-effects.
    this.mgr.events.addUserLoaded((u) => {
      this.oidcUser = u;
    });
    this.mgr.events.addUserUnloaded(() => {
      this.oidcUser = null;
      this.appUser = null;
    });
    this.mgr.events.addAccessTokenExpired(() => {
      // Best-effort silent renew, else force re-login.
      this.mgr?.signinSilent().catch(() => this.mgr?.signinRedirect());
    });

    try {
      const u = await this.mgr.getUser();
      if (u && !u.expired) {
        this.oidcUser = u;
        await this.refreshUser();
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[auth] getUser() failed:', e);
    }
    this.initialized = true;
  }

  /** Force a redirect to Keycloak for sign-in. No-op when disabled. */
  async login(returnTo?: string): Promise<void> {
    if (this.disabled || !this.mgr) return;
    await this.mgr.signinRedirect({ state: { returnTo: returnTo ?? window.location.pathname } });
  }

  /** Force a redirect to Keycloak for sign-out. No-op when disabled. */
  async logout(): Promise<void> {
    if (this.disabled || !this.mgr) return;
    await this.mgr.signoutRedirect();
  }

  /**
   * Handle the OAuth redirect back from Keycloak.
   * Returns the `returnTo` path from the OIDC `state` (or '/product').
   * Note: Does NOT call refreshUser() - the Callback component handles that.
   */
  async handleCallback(): Promise<string> {
    if (this.disabled || !this.mgr) return '/product';
    console.log('[auth] handleCallback: starting signinRedirectCallback...');
    const u = await this.mgr.signinRedirectCallback();
    console.log('[auth] handleCallback: signinRedirectCallback completed');
    this.oidcUser = u;
    const state = (u.state ?? {}) as { returnTo?: string };
    const dest = state.returnTo && state.returnTo !== '/auth/callback' ? state.returnTo : '/product';
    console.log('[auth] handleCallback: returning destination:', dest);
    return dest;
  }

  /** Fetch the app's view of the current user from /api/v1/auth/me. */
  async refreshUser(): Promise<AppUser | null> {
    if (this.disabled) {
      this.appUser = SYNTHETIC_ADMIN;
      return this.appUser;
    }
    const token = this.getAccessToken();
    if (!token) {
      console.warn('[auth] refreshUser: no access token available');
      return null;
    }
    try {
      console.log('[auth] refreshUser: fetching /api/v1/auth/me...');
      const r = await fetch(`${API_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      console.log('[auth] refreshUser: response status:', r.status);
      if (!r.ok) {
        console.warn('[auth] refreshUser: API returned', r.status);
        return null;
      }
      this.appUser = (await r.json()) as AppUser;
      console.log('[auth] refreshUser: user loaded:', this.appUser.id);
      return this.appUser;
    } catch (e) {
      console.error('[auth] refreshUser: fetch failed:', e);
      return null;
    }
  }

  getAccessToken(): string | null {
    if (this.disabled) return null;
    return this.oidcUser?.access_token ?? null;
  }

  getUser(): AppUser | null {
    return this.appUser;
  }

  getConfig(): AuthConfig | null {
    return this.config;
  }

  isReady(): boolean {
    return this.initialized;
  }
}

export const auth = new AuthClient();
