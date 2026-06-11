# fix: prevent double auth callback in React StrictMode + rotate service token

## Summary

Two small but critical fixes:

1. **Auth callback double-execution bug** — React 18 StrictMode intentionally unmounts/remounts components in dev, causing `Callback.tsx` to run twice and consume the one-time authorization code twice, leading to "Code already used" errors from Keycloak and subsequent 401s on all API requests.

2. **Service token rotation** — Replace the placeholder dev token with a proper cryptographically-secure value.

---

## Changes

### `apps/web/src/auth/Callback.tsx`

**Problem:**  
React 18's StrictMode double-mounts components in development. The `useEffect` in `Callback.tsx` was running twice:
- First run: exchanges the authorization code for tokens ✅
- Second run: tries to exchange the *same* code again ❌
- Keycloak rejects with "Code already used"
- No token gets stored in `UserManager`
- All subsequent API requests fail with 401

**Solution:**  
Add a `useRef` guard (`handledRef`) that survives the intentional unmount/remount cycle. The second `useEffect` invocation becomes a no-op.

**Technical details:**
- Removed the `cancelled` flag pattern (unnecessary with the ref guard)
- Added inline comment explaining the StrictMode behavior
- Simplified cleanup — no need for cleanup function anymore

**Code changes:**
```typescript
// Before: cancelled flag pattern
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
  return () => { cancelled = true; };
}, []);

// After: useRef guard
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
}, []);
```

---

### `.env.example`

**Problem:**  
The placeholder service token `dev-service-token-change-me-in-prod` was still in use.

**Solution:**  
Rotate to a proper 32-byte URL-safe token generated via:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**New value:**  
```
SLM_FORGE_SERVICE_TOKEN=xxxxxx
```

**Security note:**  
This is still a *development* token committed to the repo. Production deployments should generate their own unique value and keep it out of version control (use `.env.local` or a secrets manager).

---

## Impact

### Before this fix:
1. User clicks "Login" → redirected to Keycloak → logs in successfully
2. Keycloak redirects back to `/auth/callback?code=...&state=...`
3. React StrictMode runs the callback twice
4. Second run fails with "Code already used"
5. No token stored → every API request returns 401
6. User sees blank dashboard or "Unauthorized" errors

### After this fix:
1. User clicks "Login" → redirected to Keycloak → logs in successfully
2. Keycloak redirects back to `/auth/callback?code=...&state=...`
3. Callback runs once (second StrictMode mount is a no-op)
4. Token stored successfully
5. User navigated to intended destination
6. All API requests work ✅

---

## Testing

### Manual verification:
1. Start the stack with auth enabled:
   ```bash
   docker compose --profile auth up -d
   export SLM_FORGE_AUTH_ENABLED=true
   ```

2. Open the web UI in dev mode (React StrictMode active):
   ```bash
   cd apps/web && npm run dev
   ```

3. Click "Login" → complete Keycloak flow
4. Verify:
   - No "Code already used" errors in browser console
   - No 401s on subsequent API requests
   - Dashboard loads correctly
   - User badge shows correct username

### Code review:
- `useRef` pattern is the [official React recommendation](https://react.dev/learn/synchronizing-with-effects#fetching-data) for preventing double-execution in StrictMode
- No functional changes to the auth flow itself — just prevents duplicate execution

---

## Files changed

```
 2 files changed, 17 insertions(+), 8 deletions(-)
```

- `apps/web/src/auth/Callback.tsx` — add `useRef` guard against StrictMode double-mount
- `.env.example` — rotate service token to cryptographically-secure value

---

## Related issues

This fixes the root cause of the "every request 401s after login" bug that appeared after Phase M.5 (Admin UI + login) was merged. The bug was environment-specific:

- **Production builds** (`npm run build`) → StrictMode disabled → no double-mount → bug never appeared
- **Development** (`npm run dev`) → StrictMode enabled → double-mount → bug always appeared

The fix ensures consistent behavior in both environments.

---

## Deployment notes

- **No migration required** — pure client-side fix
- **No API changes** — backend unchanged
- **Safe to deploy immediately** — backwards-compatible
- **Service token rotation** — if you've already deployed with the old token, update your `.env` and restart the API container

---

## References

- [React 18 StrictMode behavior](https://react.dev/reference/react/StrictMode#fixing-bugs-found-by-double-rendering-in-development)
- [oidc-client-ts UserManager.signinRedirectCallback()](https://authts.github.io/oidc-client-ts/classes/UserManager.html#signinRedirectCallback)
- [OAuth 2.0 authorization code flow](https://datatracker.ietf.org/doc/html/rfc6749#section-4.1) — codes are one-time use by design
