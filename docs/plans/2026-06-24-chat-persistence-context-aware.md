# Plan — Context-aware persistent chat per conversation ID

> **Spec:** `docs/specs/CHAT_PERSISTENCE_CONTEXT_AWARE_SPEC.md`
> **Date:** 2026-06-24
> **Owner:** Amit
> **Branch:** to be created off `amitssahu` once the spec is approved.

---

## Honest framing

**Most of what you asked for already exists.** Each chat has its
own integer `id` (`ChatConversation.id`); messages persist in
`chat_messages` linked by that id; on every new turn the API replays
the full prior history into the LangGraph agent so the model answers
in context. You can prove it today: say *"my name is Pat"* and then
*"what's my name?"* — the model will say *"Pat"*.

What's *not* there yet, and what this plan delivers:

1. **Tool calls and their results from prior turns aren't replayed.**
   So if turn 1 looked up run #42, turn 2 doesn't remember it ever did.
2. **No token-budget guard.** Long conversations silently get
   truncated by the model when the 32k window overflows.
3. **No per-user / per-tenant scoping.** Anyone with the conversation
   id can read it. CLAUDE.md §35 wants this fixed.
4. **No observability** on chat turns from the existing Traces tab.

The plan closes those four gaps. **No new chat tab, no chat
re-architecture, no LangGraph swap.**

---

## Red-team passes (≥3 clean)

### Pass 1 — happy path + simple corrections
- **Concern:** Adding `ToolMessage` replay might confuse the LLM if a
  tool result schema changes mid-conversation.
- **Mitigation:** `ToolMessage.content` is a string; we reconstruct
  from `chat_messages.content` verbatim. No schema interpretation.
- **Concern:** Summarization at exactly the budget boundary races
  with concurrent turns on the same conversation.
- **Mitigation:** The summarization call runs *inside* the SSE
  handler, before the graph stream starts. Two concurrent streams
  on the same conversation are already unsupported (the UI only
  allows one stream per tab); we add an advisory lock check —
  `convo.last_summarized_at` time-stamp prevents double-summarize
  within 5s.

### Pass 2 — scaling + cost
- **Concern:** Summarization fires too often → 5× the Hermes cost
  per chat.
- **Mitigation:** Summarization only fires when token estimate > 75%
  of `CHAT_HISTORY_MAX_TOKENS`. With `keep_last_n=8`, the next
  trigger is many turns away. The previous summary is reused +
  extended, not regenerated from scratch.
- **Concern:** Tenant filter on chat_conversations causes a
  full-table scan.
- **Mitigation:** New `tenant_id` column is indexed; the schema
  migration adds the index.

### Pass 3 — security + failure modes
- **Concern:** Auth check on streams might block legitimate users in
  the default dev (auth-off) flow.
- **Mitigation:** `@requires` is a pass-through when
  `SLM_FORGE_AUTH_ENABLED=false` — verified pattern from existing
  endpoints.
- **Concern:** Summarization fails (Ollama down) → user's turn
  blocks indefinitely.
- **Mitigation:** Summarization call uses the existing
  `_call_ollama` retry/timeout (300s / 3 retries). On terminal
  failure, fall back to "keep last 2× `keep_last_n` messages
  verbatim, no summary" so the turn still completes — surfaces a
  WARN-level structured log line for the operator.
- **Concern:** A malicious user could exhaust storage by sending
  huge messages.
- **Mitigation:** Pre-existing FastAPI request-body limits apply.
  No new attack surface.

### Pass 4 — final review (must be clean)
- All concerns above are addressed. The remaining risks (LLM
  hallucinations in summary, qwen3 context truncation behavior)
  are pre-existing properties of the chat stack, not introduced
  by this plan.

---

## Phased deliverables (single PR, 4 commits)

### Commit 1 — Schema + migration + tests
Files:
- `apps/api/models/chat.py` (add `tenant_id`, `user_id`,
  `summary_message_id`, `last_summarized_at` to `ChatConversation`;
  `tenant_id`, `token_estimate` to `ChatMessage`).
- `apps/api/services/db.py` (`_CHAT_CONVERSATION_MIGRATIONS`,
  `_CHAT_MESSAGE_MIGRATIONS`, plumbed into `init_db()`).
- `tests/api/test_chat_schema_migration.py` — TDD red first:
  - new columns exist; legacy rows load with `NULL` / safe defaults;
  - migration is idempotent;
  - indexes present on `tenant_id`, `user_id`.

### Commit 2 — Full-fidelity history replay
Files:
- `apps/api/routers/chat.py` (extend `_load_history_as_langchain`;
  the SSE stream's persistence step writes `tool` rows for each
  `ToolMessage` produced during the turn).
- `tests/chat_agent/test_history_replay.py` — TDD red first:
  - on a seeded conversation with prior tool calls, the agent's
    input message list contains the prior `AIMessage(tool_calls=...)`
    + `ToolMessage` pair in chronological order;
  - intermediate `tool` rows are persisted after a stream completes.

### Commit 3 — Token budget + summarization
Files:
- `packages/chat_agent/history.py` (new module:
  `compose_working_context`).
- `.hermes-skills/summarize_chat_window.md` (new skill).
- `apps/api/routers/chat.py` (call `compose_working_context` before
  passing history to the graph; persist summary as
  `ChatMessage(role="summary", ...)`).
- `tests/chat_agent/test_compose_working_context.py` — pure-function
  tests for the budget math.
- `tests/api/test_chat_summarization.py` — integration test that
  with `CHAT_HISTORY_MAX_TOKENS=200` and 20 seeded turns, the next
  stream produces a `summary` row and the agent input length is
  bounded.

### Commit 4 — Auth gate + chat-turn traces
Files:
- `policies/chat.rego` (or extension to `policies/slm_forge.rego`) —
  `chat.allow_access` rule + admin override.
- `apps/api/routers/chat.py` (add `@requires` on every endpoint;
  populate `tenant_id` + `user_id` on convo create; on each turn,
  write one `HermesTrace` row with `source="chat:turn"`).
- `tests/api/test_chat_auth.py` — OPA gate tests: non-owner gets
  403; admin gets allow; auth-disabled is pass-through.
- `tests/api/test_chat_tenant_isolation.py` — two tenants, disjoint
  conversation lists.
- `tests/api/test_chat_turn_trace.py` — each turn writes one
  `hermes_traces` row with the expected metadata; renders in the
  existing `/skills/summary` aggregate as `chat:turn`.

---

## Architecture choice — MAANG-scale defensibility

- **Stateless API:** every turn is a stateless request. State lives
  in SQLite (or a future Postgres swap — the SQLModel layer is
  identical). Horizontal scale is one container away.
- **No new background services:** summarization runs *in-band* per
  turn at the budget threshold. Avoids a worker / queue.
- **Bounded prompt cost:** with `keep_last_n=8` and incremental
  summary updates, prompt size is O(1) of conversation length,
  not O(N).
- **Tenant-first data model:** every chat row carries `tenant_id`
  + the conversations carry `user_id`. Indexed for cheap filtering.
  Cross-tenant access is impossible by query construction.
- **Observability:** chat turns appear alongside Hermes skill calls
  in the existing Traces tab — no second UI to learn.
- **Failure isolation:** summarization failures degrade gracefully
  to "longer truncated context"; the turn still completes.

---

## Critical files (will be touched)

```text
apps/api/models/chat.py
apps/api/routers/chat.py
apps/api/services/db.py
packages/chat_agent/history.py            (new)
.hermes-skills/summarize_chat_window.md   (new)
policies/chat.rego                        (new) or extends existing
tests/api/test_chat_schema_migration.py   (new)
tests/api/test_chat_summarization.py      (new)
tests/api/test_chat_auth.py               (new)
tests/api/test_chat_tenant_isolation.py   (new)
tests/api/test_chat_turn_trace.py         (new)
tests/chat_agent/test_history_replay.py   (new)
tests/chat_agent/test_compose_working_context.py (new)
release/0.7.2.md                          (new)
commit_message.md                         (gitignored — single
                                          composite message at PR end)
```

---

## Verification (after implementation)

```bash
# 1. Tests
uv run pytest -q tests/api/test_chat_schema_migration.py \
                 tests/api/test_chat_summarization.py \
                 tests/api/test_chat_auth.py \
                 tests/api/test_chat_tenant_isolation.py \
                 tests/api/test_chat_turn_trace.py \
                 tests/chat_agent/test_history_replay.py \
                 tests/chat_agent/test_compose_working_context.py

# 2. Full suite stays green
uv run pytest -q

# 3. Lint + type
uv run ruff check apps/api/models/chat.py apps/api/routers/chat.py \
                  apps/api/services/db.py packages/chat_agent/history.py
uv run mypy apps packages

# 4. Web build (no UI changes here, but confirm types still align if
# the OpenAPI changes get regenerated in api.ts)
cd apps/web && npm run build
```

Manual end-to-end (`make dev` running):

1. Open the Chat tab → create a new conversation. Note its id.
2. Say *"my name is Pat"*. Then *"what's my name?"* → expect *"Pat"*.
3. Ask *"list recent runs"* → tool fires. Then *"show metrics for
   the first one"* → the agent should reference the run id from the
   previous tool result without firing `list_runs` again.
4. Re-open the page (browser reload). The conversation list still
   shows your chat by id; opening it shows full history.
5. With `CHAT_HISTORY_MAX_TOKENS=200`, fire 20 turns. After turn ~10
   inspect the conversation in the DB / API:
   `SELECT role, length(content) FROM chat_messages WHERE
   conversation_id=<id> ORDER BY id;` → at least one `role='summary'`.
6. Open the Traces tab → filter by skill `chat:turn`. Every turn
   shows up; rows fired during summarization also show a
   `skill:summarize_chat_window` companion trace.

`curl` (auth-disabled):

```bash
# Create a conversation
CID=$(curl -s -X POST http://localhost:8000/api/v1/chat/conversations \
  -H "Content-Type: application/json" -d '{"title":"plan-test"}' | jq -r .id)

# Post a user turn
curl -s -X POST http://localhost:8000/api/v1/chat/conversations/$CID/messages \
  -H "Content-Type: application/json" -d '{"content":"my name is Pat"}'

# Stream the response (one-shot for sanity)
curl -N "http://localhost:8000/api/v1/chat/conversations/$CID/stream"

# Inspect the chat-turn trace
curl -s "http://localhost:8000/api/v1/hermes/traces?skill=chat:turn&limit=5" | jq
```

---

## Definition of Done (gate per CLAUDE.md)

- [ ] Spec + plan committed; ≥3 clean red-team passes (documented above).
- [ ] All 7 acceptance criteria from the spec demonstrably met.
- [ ] Tests written first (TDD), all green; ≥90% coverage on touched modules.
- [ ] No hardcoded values; `CHAT_HISTORY_MAX_TOKENS` env-driven + fail-fast.
- [ ] OPA gate enforced on every chat endpoint; tenant isolation tested.
- [ ] Migrations additive, nullable, reversible; verified on a
      copy of `data/slm_forge.db`.
- [ ] No `*_v#` modules; existing `ChatConversation` / `ChatMessage`
      extended in place.
- [ ] `README.md` Chat section updated; `release/0.7.2.md` appended;
      commit via `commit_message.md`.
- [ ] Change summary + UI click steps + `curl` examples (above)
      handed back to the user.
