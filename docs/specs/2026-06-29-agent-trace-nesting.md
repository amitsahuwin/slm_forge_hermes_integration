# Phase B Spec — Agent traces with nested skill spans

> **Status:** approved · **Date:** 2026-06-29 · **Owner:** Amit
> **Plan:** `docs/plans/2026-06-29-agent-trace-nesting.md`
> **Branch:** `feat/agent-trace-nesting` off `main` after Phase A merges.

---

## 1. Problem

`hermes_traces` (`apps/api/models/hermes_trace.py:18-62`) is flat:

- no `parent_span_id`, no `trace_id`,
- span "kind" is inferred from a free-form `source` string like
  `"skill:propose_hyperparam_mutation"`,
- run/session nest only by correlation IDs (`run_id`, `session_id`).

Each LangGraph agent (`packages/agents/runner.py`) chains 2-4 Hermes
skills and emits one trace **per skill call**. The agent run itself
emits nothing. The Traces tab therefore shows only skill rows —
the agent as a logical unit is invisible.

---

## 2. Requirements

### R1 — Schema (additive, reversible)

`hermes_traces` gains four columns:

| Column          | Type | Default      | Index |
|-----------------|------|--------------|-------|
| `kind`          | TEXT | `'skill'`    | no    |
| `trace_id`      | TEXT | NULL         | yes   |
| `parent_span_id`| TEXT | NULL         | no    |
| `agent_run_id`  | TEXT | NULL         | yes   |

Existing rows backfill `kind='skill'`, `trace_id=id::text` (each row
becomes its own root trace). New rows must populate all four.

Migrations go into a new `_TRACE_MIGRATIONS` list in
`apps/api/services/db.py`, following the same shape as `_RUN_MIGRATIONS`.

### R2 — Tracing context manager

New module `apps/api/services/tracing.py`:

```python
async with trace_span(
    kind: Literal["agent", "skill", "tool"],
    name: str,
    *,
    run_id: int | None = None,
    session_id: int | None = None,
    agent_run_id: str | None = None,
    **attrs,
) as span:
    ...
```

- On enter: if no current `trace_id` in the contextvar, generate a UUIDv7
  and become the root. Push `(trace_id, span_id)` onto the contextvar
  stack. Insert a `hermes_traces` row with `kind`, `source=name`,
  `trace_id`, `parent_span_id` (or NULL if root), `agent_run_id`,
  `started_at=now()`.
- On exit (normal or exception): update the row with `duration_ms`,
  `error` (if any), `response_body` (if attached via `span.set_result()`),
  `status`. Pop the contextvar stack.

### R3 — Wrap agent runs

`packages/agents/runner.py` exposes:

```python
async def stream_agent(name, *args, **kwargs):
    agent_run_id = str(uuid7())
    async with trace_span(kind="agent", name=name, agent_run_id=agent_run_id):
        ...  # existing LangGraph compile/stream
```

LangGraph nodes that call Hermes skills do **not** change. The
existing trace writer in those skills picks up `trace_id` and the parent
span from the contextvar automatically.

### R4 — API: tree-grouped traces

`GET /api/v1/hermes/traces`:

- New query params: `group_by=trace` (default `none`), `kind=agent|skill|tool`.
- When `group_by=trace`: returns `list[TraceTreeRow]`, each with
  `children: list[TraceRow]` for spans sharing the same `trace_id`,
  ordered by `started_at`.
- When `group_by=none`: existing flat shape unchanged.
- New endpoint `GET /api/v1/hermes/traces/{trace_id}` returns the full
  tree for one trace.

### R5 — Frontend tree view

`apps/web/src/pages/Traces.tsx`:

- Toggle in the toolbar: **Tree** | **Flat**. Default Tree.
- In Tree mode, agent rows render as expandable headers showing
  `name`, `duration_ms`, child count. Expanding shows nested skill spans
  with indentation. Status (success/error) shown per row.
- The right-hand detail pane shows the selected node's
  `request_body` / `response_body` like today.

### R6 — Tests

- `tests/api/test_trace_nesting.py` — contextvar correctness: parent
  span set, child inherits trace_id, root has NULL parent.
- `tests/api/test_traces_router.py` — `?group_by=trace` shape.
- `tests/api/test_agents_run.py` (extends Phase A test) — agent run
  POST creates one parent + N child rows with shared `trace_id`.
- `apps/web/src/pages/__tests__/Traces.tree.test.tsx` — expand/collapse,
  toolbar toggle.

---

## 3. Non-goals

- Distributed tracing across worker subprocesses (deferred).
- OpenTelemetry export (deferred — internal tracing only).
- Tool-call spans inside skills (kind="tool" reserved for future).

---

## 4. Acceptance criteria

- All R6 tests written first; all pass.
- Manual: trigger an agent run; Traces tab shows the agent row;
  expand to see 2-4 nested skill rows; row count and chip work
  consistently with Phase A.
- Backwards compat: existing flat-view consumers (e.g. `traces/skills/summary`)
  continue to work.
- `uv run pytest -q` green; coverage ≥90% on changed modules.
