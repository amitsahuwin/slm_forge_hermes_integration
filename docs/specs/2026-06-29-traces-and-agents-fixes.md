# Phase A Spec — Traces filter + Agents "Run agent" fixes

> **Status:** approved · **Date:** 2026-06-29 · **Owner:** Amit
> **Parent:** `/Users/amitsahu/.claude/plans/hazy-stargazing-spindle.md`
> **Plan:** `docs/plans/2026-06-29-traces-and-agents-fixes.md`
> **Branch:** `fix/traces-filter-and-agents-run` off `amitssahu`

---

## 1. Problem

### 1.1 Traces filter chip lies about active-filter count

`apps/web/src/pages/Traces.tsx:226-234` computes:

```ts
const activeFilterCount =
  (skillFilter.size > 0 ? 1 : 0) +
  (statusFilter ? 1 : 0) +
  (timeRange !== 'all' ? 1 : 0) +
  (minDuration ? 1 : 0) +
  (runIdFilter ? 1 : 0) +
  (sessionIdFilter ? 1 : 0) +
  (sourceFilter ? 1 : 0);
```

The whole **Skill** category counts as 1 regardless of how many skills the user
has clicked. Selecting 5 skills + a status filter shows "2 filters" but feels
like 6. Users assume the chip is broken.

### 1.2 Left "Skill Activity" panel click may not toggle the filter

User report: "selecting the Activity the filters count is not increasing".
Two possible root causes:

- click handler is not wired to mutate `skillFilter`, OR
- handler mutates `skillFilter` but `activeFilterCount` is what the user reads
  and it stays at 1 (covered by 1.1).

Both must be verified; both must work after this PR.

### 1.3 "Run agent" produces no visible activity

`apps/web/src/pages/Agents.tsx:145-186` POSTs to `/api/v1/agents/{name}/run`
and hand-rolls an SSE reader over `fetch + ReadableStream`. The endpoint
exists (`apps/api/routers/agents.py:220-239`) and returns
`EventSourceResponse`. The button currently shows nothing — no stage
updates, no error.

Likely causes (to be diagnosed before the fix):

- the `Authorization: Bearer` header set by `authFetch`
  (`apps/web/src/lib/api.ts:17`) is dropped because the call goes through a
  bare `fetch` rather than `authFetch`,
- `_prepare_args()` rejects the loose per-agent payload schema silently and
  returns 422 before SSE ever opens,
- `stream_agent` raises an exception inside `packages/agents/runner.py` and
  the SSE `error` event is emitted but the frontend does not surface it.

---

## 2. Requirements

### R1 — Filter chip semantics
- `activeFilterCount = skillFilter.size + …` (sum the set size, not `>0`).
- Label: `"{n} filter(s) active"` next to the chip.
- Right-pane row count relabel: `"{n} matching trace(s)"`.

### R2 — Skill Activity click wires to `skillFilter`
- Clicking a row in the left panel toggles that skill in/out of
  `skillFilter` and triggers the existing `useEffect`-driven re-fetch.
- Selected rows have a visible active state (highlight + ✓).

### R3 — Agents "Run agent" works end-to-end
- Frontend uses `authFetch` (or equivalent) for the run POST so the JWT is
  attached. (After Phase C, this is mandatory.)
- SSE `error` events surface as a toast and `console.error`.
- 4xx responses from the run endpoint are parsed (FastAPI returns
  `{detail: ...}`) and shown as a non-dismissible error banner above the
  output panel.
- Backend `_prepare_args()` errors return a structured 422 with
  `{detail: {agent, missing_fields, hint}}` rather than a free-form string.

### R4 — Tests precede implementation
- `apps/web/src/pages/__tests__/Traces.filterCount.test.tsx` — RTL +
  Vitest. Mount Traces with stub data, select 3 skill rows + a status,
  assert chip reads `"4 filter(s) active"`.
- `apps/web/src/pages/__tests__/Traces.activityClick.test.tsx` — clicking
  a Skill Activity row dispatches a state change that increments
  `activeFilterCount`.
- `apps/web/src/pages/__tests__/Agents.runErrors.test.tsx` — mock fetch to
  return a stream with an SSE `error` event; assert the toast is rendered.
- `tests/api/test_agents_run_validation.py` — POST a payload missing a
  required field; assert 422 with the new structured body.

---

## 3. Non-goals

- Adding new filters or new agent UIs.
- Changing the agent input schemas (deferred to Phase C where identity
  fields are added).
- Trace nesting / agent traces (Phase B).

---

## 4. Acceptance criteria

- All four tests in R4 are written first and fail; then pass after the
  implementation.
- Manual: stack up, log in (any mode), open Traces, click 3 activity rows,
  see chip "3 filter(s) active"; click an existing chip off, see chip "2";
  apply a status filter, see "3"; row count under filtered list reads
  "N matching trace(s)".
- Manual: open Agents tab, click "Run agent" on `experiment_recommender`
  with a deliberately empty payload — see a structured error banner;
  click again with a valid payload — see SSE stage events stream in.
