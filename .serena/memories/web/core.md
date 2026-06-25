# apps/web — React frontend

Runs in Docker (`:5173`). React 19 + Vite 6 + TS 5.7 + Tailwind 3.4 + react-router 7.

## Layout (under `apps/web/src/`)
- `App.tsx` — top-level routes.
- `pages/` — route components. Key: `NewExperiment.tsx`, `NewRun.tsx`.
- `components/` — reusable widgets, e.g. `HermesSkillButton.tsx`.
- `lib/api.ts` — HTTP client + endpoint wrappers; `lib/backends.ts` — backend metadata for dropdowns.

## Build gate
- `npm run build` = `tsc --noEmit && vite build`. TS errors block.
- Dev: `npm run dev` (separate from Docker UI).

## Notes
- Model dropdowns must be driven from `/api/v1/models/v2` filtered by the selected backend (`backends.ts`).
- Auth flow via `oidc-client-ts` against Keycloak; charts via `recharts`.
- Admin panel at `/admin/users` requires `make auth ENABLED=true` + admin role.
