# Plan — Phase B: Agent traces with nested skill spans

> **Spec:** `docs/specs/2026-06-29-agent-trace-nesting.md`
> **Date:** 2026-06-29 · **Owner:** Amit
> **Branch:** `feat/agent-trace-nesting` off `main` after Phase A.
> **Parent plan:** `/Users/amitsahu/.claude/plans/hazy-stargazing-spindle.md`

---

## Red-team passes

### Pass 1 — schema integrity
- **Concern:** backfilling `trace_id = id::text` may collide with
  later-generated UUIDv7 strings.
- **Mitigation:** UUIDv7 strings always contain a hyphen at position 14;
  backfilled integer-string IDs do not. Verifier code rejects any
  `trace_id` without the expected shape on insert.

### Pass 2 — contextvar lifecycle
- **Concern:** `contextvars` do not automatically propagate across
  `asyncio.create_task` boundaries unless the task is created from
  within the current context.
- **Mitigation:** all skill calls within an agent run inherit context
  via `await` chains (no detached tasks). For places that create tasks,
  we explicitly use `loop.create_task(coro, context=copy_context())`.
- **Concern:** an unhandled exception inside `trace_span` could leave
  the contextvar stack in a corrupted state.
- **Mitigation:** the context manager uses `try/finally` for the pop;
  unit-tested in `test_trace_nesting.py::test_exception_unwinds_stack`.

### Pass 3 — performance / SQLite contention
- **Concern:** each agent run now produces 1 + N writes to `hermes_traces`
  instead of N. With 5 agents × 4 skills × concurrent runs = 25 writes/s
  best case, fine for SQLite; bursty agent runs could push to ~50 writes/s.
- **Mitigation:** writes are already async; use `asyncio.Lock` only inside
  the writer if WAL-mode contention shows up in test_streaming_upload
  load.

### Pass 4 — clean (target)
- API extension is backwards-compatible (new optional query params).
- New columns default NULL; old code paths unaffected.
- Existing skill-only traces continue to render.

---

## Implementation steps

### Step 1 — Tests (RED)

```bash
# Backend
touch tests/api/test_trace_nesting.py
touch tests/api/test_traces_router.py
# extend tests/api/test_agents_run.py (created in Phase A)

# Frontend
touch apps/web/src/pages/__tests__/Traces.tree.test.tsx

uv run pytest tests/api/test_trace_nesting.py tests/api/test_traces_router.py -q
cd apps/web && npm test -- --run Traces.tree
# all RED
```

### Step 2 — B1: Schema migration

Edit `apps/api/services/db.py`: add `_TRACE_MIGRATIONS = [...]` and wire
it into `init_db()` parallel to `_RUN_MIGRATIONS`. Backfill query:

```sql
UPDATE hermes_traces
SET kind='skill', trace_id=CAST(id AS TEXT)
WHERE trace_id IS NULL;
```

Edit `apps/api/models/hermes_trace.py`: add the four fields with
`Field(default=None, index=True)` for `trace_id` and `agent_run_id`.

### Step 3 — B2: `apps/api/services/tracing.py`

New module. Implements `trace_span` as an async context manager backed
by `contextvars.ContextVar[list[tuple[str, str]]]`. Re-export the
existing trace-write helper (currently inlined in `packages/agents/runner.py`
and `packages/research/...`) — move it here.

### Step 4 — B3: Wrap agent runs

Edit `packages/agents/runner.py`:

```python
async def stream_agent(name, *args, **kwargs):
    agent_run_id = uuid7_str()
    async with trace_span(kind="agent", name=name, agent_run_id=agent_run_id) as span:
        async for event in _existing_stream(name, *args, **kwargs):
            yield event
        span.set_result(...)  # final recommendation
```

Replace any direct trace writes inside `runner.py` (per-step writes for
the LangGraph nodes) with `trace_span(kind="skill", ...)` if not already
emitted by the Hermes client.

### Step 5 — B4: API + frontend

Edit `apps/api/routers/traces.py`:
- add `group_by` and `kind` query params,
- new endpoint `GET /api/v1/hermes/traces/{trace_id}`.

Edit `apps/web/src/pages/Traces.tsx`:
- add the Tree | Flat toolbar toggle (default Tree),
- new component `TraceTreeRow` that renders an agent row + nested
  children with expand/collapse via `useState<Set<string>>` of expanded
  trace_ids.

### Step 6 — verify

```bash
uv run pytest tests/api/test_trace_nesting.py tests/api/test_traces_router.py tests/api/test_agents_run.py -q
cd apps/web && npm test -- --run Traces.tree && npm run build
uv run pytest -q   # full suite
uv run ruff check --fix <changed files>
uv run mypy apps packages
```

### Step 7 — commit + PR

Same flow as Phase A. PR title: `feat: nested agent traces in Traces tab`.

---

## Files modified

- `apps/api/services/db.py` (add `_TRACE_MIGRATIONS`)
- `apps/api/models/hermes_trace.py` (add 4 fields)
- `apps/api/services/tracing.py` (new)
- `apps/api/routers/traces.py` (extend)
- `packages/agents/runner.py` (wrap with `trace_span`)
- `apps/web/src/pages/Traces.tsx` (tree view + toggle)
- `tests/api/test_trace_nesting.py` (new)
- `tests/api/test_traces_router.py` (new)
- `tests/api/test_agents_run.py` (extend)
- `apps/web/src/pages/__tests__/Traces.tree.test.tsx` (new)
- `docs/specs/2026-06-29-agent-trace-nesting.md`
- `docs/plans/2026-06-29-agent-trace-nesting.md`
- `release/PR-2.md` (new)

## Definition of Done

- [ ] Spec + plan committed
- [ ] 4 test files green
- [ ] Coverage ≥90% on `tracing.py`, `traces.py`, `runner.py`
- [ ] Manual: click "Run agent" → see top-level agent row in Traces with
      nested children
- [ ] `ruff` + `mypy` + `npm run build` green
- [ ] Release notes; PR opened
