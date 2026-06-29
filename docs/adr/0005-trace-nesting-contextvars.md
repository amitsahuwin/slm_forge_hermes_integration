# ADR-0005 — Trace nesting via `contextvars`, not a new spans table

- **Status:** Accepted
- **Date:** 2026-06-29
- **Phase:** B (agent trace nesting)
- **Supersedes:** the flat-`hermes_traces` design from Phase N.3
- **Related:** `docs/specs/2026-06-29-agent-trace-nesting.md`,
  ADR-0001 (Hermes bridge chokepoint)

## Context

The Traces tab showed only skill-level spans because `hermes_traces`
was a flat table (one row per Hermes skill call) with no `parent_id`
or `trace_id` columns. The agent runs that chain 2–4 skills had no
top-level representation; clicking "Run agent" on the Agents page
produced rows for the child skills but no parent — the user couldn't
tell the agent had fired at all.

Two recovery routes were obvious:

1. **A new `spans` table modelled on OpenTelemetry**, with `trace_id`,
   `span_id`, `parent_span_id`, `kind`, plus a foreign key on
   `hermes_traces` so legacy callers continued to work.
2. **Extend `hermes_traces` in place** with nullable
   `trace_id`/`parent_span_id`/`span_id`/`kind`/`agent_run_id`
   columns, and introduce a `trace_span` context manager that pushes a
   `(trace_id, span_id)` tuple onto a `contextvars.ContextVar`. Legacy
   write paths inspect the context and inherit, or stamp a fresh
   trace_id if no context is active.

## Decision

Extend `hermes_traces` in place + `contextvars`-backed `trace_span`
context manager. No new table; no OTel wire format; no third-party
tracing dependency.

The trace lives entirely inside the request/agent invocation as a
contextvar stack. Every write through `_record_trace` (in the
ratchet/hermes bridge) reads the stack and inherits the active
`trace_id`/`parent_span_id` if any. The agent runner wraps each
`stream_agent` call in `trace_span(kind="agent", agent_run_id=...)`;
when the LangGraph state machine is offloaded to
`loop.run_in_executor`, the runner explicitly
`contextvars.copy_context()`s into the worker thread so child skill
calls running on the threadpool inherit the trace.

The model file gains five columns:

| Column           | Default          | Index |
|------------------|------------------|-------|
| `kind`           | `'skill'`        | yes   |
| `trace_id`       | `NULL` (root)    | yes   |
| `parent_span_id` | `NULL` (root)    | —     |
| `span_id`        | `NULL`           | —     |
| `agent_run_id`   | `NULL`           | yes   |

The router gets two new shapes: `?group_by=trace` returns a
`TraceTreeRow[]` (root + inline `children`), and `/by-trace/{trace_id}`
returns one tree. The Traces tab renders agent spans as expandable
parent rows in Tree mode.

## Consequences

- **Pro — zero migration risk for existing data.** All new columns are
  nullable; legacy rows backfill to `kind='skill'` with NULL trace
  fields and render in Flat view exactly as before.
- **Pro — no new infra.** No spans table, no OTel collector, no
  Jaeger. The trace data is just rows in the existing table.
- **Pro — contextvars-on-asyncio is precise.** Each agent run gets a
  fresh stack scoped to the SSE generator; the runner's
  `copy_context()` hop is the only manual plumbing needed when
  crossing the thread boundary.
- **Pro — the writer stays best-effort.** `trace_span` writes inside a
  `try`/`except` and logs `debug` on failure, matching
  `_record_trace`'s existing behaviour. Trace persistence cannot crash
  an agent.
- **Con — querying nested traces requires app-side joining.** Tree
  shape is built in `_build_trees`, not via a recursive CTE.
  Acceptable at the lab's scale (single-digit thousands of spans);
  if it stops being acceptable, the same column shape can feed a
  generated `parent_id` FK + recursive query without re-extracting
  data.
- **Con — we don't get OTel-compatible export for free.** A future
  ADR can map this shape to OTel if/when external collection becomes
  a requirement.

## Alternatives considered

1. **New `spans` table modelled on OpenTelemetry.** Cleaner separation
   but doubles the storage for what is mostly the same fields, forces
   every existing trace consumer to join across two tables, and
   requires a non-trivial backfill. Rejected because the column-add
   path is strictly less work for the same observability outcome.
2. **OpenTelemetry SDK + Jaeger collector.** Industry-standard but
   adds a new long-running container, plus an export protocol the
   lab doesn't otherwise need. Rejected as YAGNI for a local-first
   lab whose Traces tab is the only consumer today.
3. **Single shared `trace_id` carried as a query parameter through the
   ratchet/agent chain.** Avoids contextvars entirely but requires
   threading the ID through every function signature. Rejected
   because it spreads the cross-cutting concern across the whole
   codebase.

## Implementation

- `apps/api/services/db.py:_HERMES_TRACE_MIGRATIONS` — five appended
  rows (`kind`, `trace_id`, `parent_span_id`, `span_id`,
  `agent_run_id`), all nullable / defaulted.
- `apps/api/models/hermes_trace.py` — matching SQLModel fields.
- `apps/api/services/tracing.py` (new) — `trace_span` context manager
  + `Span` dataclass + best-effort row writer.
- `packages/agents/runner.py:stream_agent` — wraps the executor call
  in `trace_span(kind="agent", agent_run_id=...)`. Uses
  `contextvars.copy_context()` to propagate into the threadpool.
- `packages/ratchet/hermes_bridge.py:_record_trace` — reads
  `_span_stack` from `apps.api.services.tracing` and inherits
  `trace_id` + `parent_span_id` when present; falls back to a fresh
  root trace_id otherwise.
- `apps/api/routers/traces.py` — adds `group_by`, `kind`,
  `agent_run_id` query params; `_build_trees` helper;
  `GET /by-trace/{trace_id}` endpoint.
- `apps/web/src/pages/Traces.tsx` — Tree | Flat toggle (default Tree)
  + `TreeNode` component.
- Tests: `tests/api/test_trace_nesting.py` (6 cases including
  exception-unwinds-stack), `tests/api/test_traces_router.py` (5
  cases), `tests/api/test_agents_trace_wrap.py` (2 cases).