# Plan — Phase A: Traces filter + Agents "Run agent" fixes

> **Spec:** `docs/specs/2026-06-29-traces-and-agents-fixes.md`
> **Date:** 2026-06-29 · **Owner:** Amit
> **Branch:** `fix/traces-filter-and-agents-run` off `amitssahu`
> **Parent plan:** `/Users/amitsahu/.claude/plans/hazy-stargazing-spindle.md`

---

## Honest framing

These are three small UI/UX bugs that bundle into one PR. The risk profile
is low; the danger is making the fix bigger than the problem. We touch
~6 files. No schema changes, no auth changes.

The trace-nesting work that would make the Agents fix visually obvious
("see a top-level agent row in Traces when you click Run") is deferred to
**Phase B** so this PR stays small.

---

## Red-team passes

### Pass 1 — happy path
- **Concern:** chip sum `skillFilter.size + …` could now show a count
  larger than the row count, looking weirder than before.
- **Mitigation:** explicit labels separate filter count from result count;
  copy: `"N filter(s) active"` vs `"N matching trace(s)"`.

### Pass 2 — error surfacing
- **Concern:** surfacing every backend error as a toast spams the UI for
  expected validation failures.
- **Mitigation:** validation failures (422 with structured body) render as
  a single inline banner above the agent's output panel and clear when the
  user edits any input. Real errors (500 / SSE `error`) get a toast.

### Pass 3 — regression
- **Concern:** if `activeFilterCount` is consumed by anything outside the
  chip (analytics, telemetry), the semantic change breaks downstream.
- **Mitigation:** `rg "activeFilterCount" apps/web/src/` — confirmed in
  the explore pass that the variable is local to Traces.tsx. Verify again
  at implementation time.

### Pass 4 — clean (target)
- All three fixes are file-local. The endpoints they touch are stable.
- No DB writes, no migrations, no auth-path changes.

---

## Implementation steps

### Step 1 — Tests (RED)

Write the four tests in spec §R4 against the current code. Run them:

```bash
cd apps/web && npm test -- --run Traces.filterCount
cd apps/web && npm test -- --run Traces.activityClick
cd apps/web && npm test -- --run Agents.runErrors
uv run pytest tests/api/test_agents_run_validation.py -q
```

All four MUST fail before any implementation.

### Step 2 — A1 + A2: Traces filter

- Edit `apps/web/src/pages/Traces.tsx`:
  - Change `(skillFilter.size > 0 ? 1 : 0)` → `skillFilter.size`.
  - Add labels `"N filter(s) active"` + `"N matching trace(s)"`.
  - Confirm/wire the Skill Activity row click → `toggleSkillFilter()`
    (the existing helper if present; otherwise add one).
- Run tests; verify the two web tests pass.

### Step 3 — A3: Agents Run reliability

- Edit `apps/web/src/pages/Agents.tsx`:
  - Replace bare `fetch` for the run POST with `authFetch` (already in
    `apps/web/src/lib/api.ts`).
  - Parse SSE `error` events from the stream; surface to toast +
    `console.error`.
  - On 4xx response, parse FastAPI's `{detail: ...}`. If detail is a
    `{missing_fields: [...], hint: string}` object (per backend change
    below), render an inline banner with the fields listed.
- Edit `apps/api/routers/agents.py` `_prepare_args` (located near line
  ~140, find with `rg "_prepare_args" apps/api/routers/agents.py`):
  - On missing/invalid field, raise
    `HTTPException(status_code=422, detail={"agent": name, "missing_fields": [...], "hint": "..."})`.
- Run all four tests; verify all green.

### Step 4 — Lint, type, full suite

```bash
uv run ruff check --fix apps/api/routers/agents.py tests/api/test_agents_run_validation.py
uv run mypy apps packages
cd apps/web && npm run build         # tsc + vite build
uv run pytest -q
```

### Step 5 — Manual verification

Per spec §4 acceptance criteria.

### Step 6 — Commit + PR

```bash
# Write commit_message.md (gitignored)
git checkout -b fix/traces-filter-and-agents-run
git add -A apps/web/src/pages/Traces.tsx \
            apps/web/src/pages/Agents.tsx \
            apps/web/src/pages/__tests__/ \
            apps/api/routers/agents.py \
            tests/api/test_agents_run_validation.py \
            docs/specs/2026-06-29-traces-and-agents-fixes.md \
            docs/plans/2026-06-29-traces-and-agents-fixes.md
git commit -F commit_message.md
git push -u origin fix/traces-filter-and-agents-run
gh pr create --title "fix: Traces filter chip semantics + Agents Run reliability" --body-file release/PR-1.md
```

---

## Files modified

- `apps/web/src/pages/Traces.tsx`
- `apps/web/src/pages/Agents.tsx`
- `apps/web/src/pages/__tests__/Traces.filterCount.test.tsx` (new)
- `apps/web/src/pages/__tests__/Traces.activityClick.test.tsx` (new)
- `apps/web/src/pages/__tests__/Agents.runErrors.test.tsx` (new)
- `apps/api/routers/agents.py`
- `tests/api/test_agents_run_validation.py` (new)
- `docs/specs/2026-06-29-traces-and-agents-fixes.md`
- `docs/plans/2026-06-29-traces-and-agents-fixes.md`
- `release/PR-1.md` (new — release notes)

## Definition of Done

- [ ] Spec + plan committed
- [ ] 4 tests written first, all green
- [ ] `uv run pytest -q` green; `npm run build` green
- [ ] Manual acceptance steps pass
- [ ] PR opened; release/PR-1.md written
