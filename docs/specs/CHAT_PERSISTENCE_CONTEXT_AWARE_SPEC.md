# Spec — Context-aware persistent chat per conversation ID

> **Status:** draft — awaiting implementation approval · **Date:** 2026-06-24
> **Owner:** Amit
> **Companion plan:** `docs/plans/2026-06-24-chat-persistence-context-aware.md`

---

## 1. Problem

The user's ask: *"each chat has its own ID; each ID maintains its own chat
history; from now onwards whenever I ask any question in any chat it should
get the context from its history and answer accordingly."*

The straightforward read is: "make chats context-aware from their own
history." A code audit shows **that contract already works for happy-path
short conversations** but breaks in four subtle ways the user will hit as
soon as they use the feature seriously:

### 1.1 What already works (don't rebuild)

- Each chat is a row in `chat_conversations` (`apps/api/models/chat.py:13`)
  with an auto-incrementing `id` — the "chat ID" the user wants.
- Every message is a row in `chat_messages` linked by `conversation_id`
  (`apps/api/models/chat.py:21`), preserving the full turn-by-turn history.
- On every new turn, `chat.stream_conversation` (`apps/api/routers/chat.py:257`)
  loads the conversation's full history via `_load_history_as_langchain`
  (L235) and passes it as `initial.messages` to the LangGraph agent
  (`packages/chat_agent/graph.py:304` `stream_response`). So the LLM
  *does* see prior `HumanMessage` and `AIMessage` turns and replies in
  context. The user can verify this today: ask "what did I just ask?"
  and the model answers.

### 1.2 Gap 1 — Tool calls and tool results from prior turns are dropped

`_load_history_as_langchain` (`apps/api/routers/chat.py:248-252`) replays
only `user` and `assistant` rows; the comment explicitly says
*"tool/system messages are not replayed"*. Symptom: in a turn where the
agent calls `list_runs` and shows the result, the next turn loses any
memory that the tool was already called or what it returned. The user
asking *"now show me the metrics for run 42"* gets a fresh tool call
into the dark — even though run 42's row is sitting in the prior
`ToolMessage`. This breaks the "context-aware" contract in the most
common multi-turn shape: action → follow-up.

### 1.3 Gap 2 — No token-budget guard rail

`qwen3:30b-a3b` (the default `CHAT_MODEL`) has a ~32k-token context
window. The current code replays *every* historical message verbatim
(`_load_history_as_langchain` — no truncation, no summarization). At
~150 tokens per turn that fills in roughly 200 turns of conversation;
with verbose tool outputs (`list_runs` returning 50 rows) it can blow
in 10–20 turns. When it overflows, the LLM silently truncates from the
front — losing the user's earliest framing, exactly the part labelled
"context from its history" in the request. No log, no UI hint.

### 1.4 Gap 3 — No tenant / user boundary

`chat_conversations` has no `tenant_id` and no `user_id`. Anyone with
the conversation id can read or post to it via the existing endpoints
(no `@requires` gate on `/chat/conversations/{id}/...`). The repo's
own multi-tenancy rule (CLAUDE.md §35) calls this non-negotiable:
*"thread a tenant boundary through data/queries/caches/logs from day
one."* Today the chat router is the only first-class FastAPI surface
that violates this rule.

### 1.5 Gap 4 — No conversation observability

There's no Traces-tab equivalent for chat: an operator cannot answer
"is the chat agent thrashing on a stale prior turn?", "what context
went into this reply?", "did the agent drop tool results between
turns?" without scraping Loki. The Hermes Skill-Activity view shipped
in 0.7.1 covers Hermes/Ollama traffic but not LangGraph chat turns.

---

## 2. Goals & non-goals

### Goals
- **G1.** Every new message on conversation `X` is answered with full
  awareness of `X`'s prior turns, *including tool calls and tool
  results from earlier turns in the same conversation* (closes 1.2).
- **G2.** Long conversations stay coherent: when the working context
  approaches the model's window, the oldest turns are summarized in
  place — not silently dropped (closes 1.3).
- **G3.** A user only sees / posts to conversations they own; tenants
  are isolated (closes 1.4).
- **G4.** Operators can inspect what context went into each turn from
  the existing Traces tab (closes 1.5) — no new tab needed.
- **G5.** No regression on the happy path: existing conversations
  continue to work; existing `POST /conversations` / `POST
  /conversations/{id}/messages` / `GET /conversations/{id}/stream`
  contracts unchanged.

### Non-goals (explicit)
- **NG1.** Cross-conversation memory ("the agent should remember what
  I said in another chat"). The user explicitly said *"each ID will
  maintain its own chat history"* — conversations are isolated.
  A future Spec can layer vector-recall on top if needed.
- **NG2.** Editing or deleting prior messages from a conversation
  (mutating history). Append-only.
- **NG3.** Voice / multimodal turns. Text only.
- **NG4.** Background/agentic actions outside of a user turn.
- **NG5.** Migrating away from LangGraph or qwen3:30b-a3b.

---

## 3. Requirements

### R1 — Full-fidelity history replay (closes Gap 1)
- `_load_history_as_langchain` (and any successor) replays `tool`
  messages alongside `user` and `assistant`. Each `ChatMessage` row
  with `role="assistant"` and a non-NULL `tool_calls_json` reconstructs
  an `AIMessage(tool_calls=...)`; each subsequent `role="tool"` row
  reconstructs a `ToolMessage(content=..., tool_call_id=...)`. The
  graph's `assemble_response` then has the full `ToolMessage` chain on
  re-entry.
- New `ChatMessage.role` values are persisted as they happen: the
  current `post_message` only writes `user`; the SSE stream's persist
  step only writes `assistant`. After R1 the stream also writes
  intermediate `tool` rows so they round-trip on next turn.

### R2 — Token-budget guard + summarization (closes Gap 2)
- A new pure function
  `chat_history.compose_working_context(messages, max_tokens) → (kept_messages, summary | None)`
  applies a "keep the last K messages verbatim, summarize the rest"
  policy. `max_tokens` is sourced from
  `CHAT_HISTORY_MAX_TOKENS` env var (default `24576` = 75% of the 32k
  qwen3:30b-a3b window; fail-fast validated at startup per CLAUDE.md
  §23).
- Summaries are produced by a *single Hermes skill call* using a new
  `summarize_chat_window` skill (`.hermes-skills/summarize_chat_window.md`).
  The resulting `SystemMessage(content=<summary>)` is prepended to
  the kept messages and persisted as `ChatMessage(role="summary",
  content=<summary>, tool_calls_json=NULL)` so it round-trips on the
  next turn. Subsequent summarizations summarize the *new* tail on top
  of the previous summary — bounded incremental cost.
- Token counting uses a cheap heuristic (`len(content) // 4`) — exact
  tokenization is not necessary for the budget guard; cheaper than
  loading a tokenizer in the API container.
- Summarization runs *before* the stream emits its first token, so the
  user perceives a single round-trip per turn (not two).

### R3 — Tenant + user scoping (closes Gap 3)
- `chat_conversations` gains `tenant_id: str = "default"` (indexed)
  and `user_id: str | None = None` (indexed). Both are additive,
  nullable for back-compat, populated from the existing
  `tenant_id_ctx` and from `request.state.user.id` at create time.
- `chat_messages` gains `tenant_id` denormalized (so message queries
  can filter without a join) — same pattern as `hermes_traces`.
- A new OPA policy `chat.allow_access(user, conversation_id)`:
  `true` if `conversation.tenant_id == user.tenant_id` AND
  (`conversation.user_id is None` OR `conversation.user_id == user.id`
  OR `"admin" in user.roles`). Wired via `@requires("read", "chat")`
  / `@requires("write", "chat")` on every chat endpoint.
- When auth is disabled (the default dev mode), the gate is a
  pass-through — existing behavior unchanged.

### R4 — Chat-turn traces in the Traces tab (closes Gap 4)
- Each LangGraph turn records one `HermesTrace` row (the existing
  table) with `source="chat:turn"`, `request_body` = JSON of
  `{conversation_id, working_context_summary_token_estimate,
   message_count_in, summarization_fired}`, `response_body` = final
  assistant text + tool result count, `run_id` / `session_id` =
  NULL, `duration_ms` = total wall-clock of the graph stream. This
  lights up the existing Skill-Activity view with `chat:turn` as
  another "skill" (no new UI), and lets the operator answer "did
  the agent summarize this turn?".
- The Hermes `summarize_chat_window` skill itself produces a
  `source="skill:summarize_chat_window"` trace via the normal
  Hermes bridge — already free from infrastructure perspective.

### R5 — Schema migration safety
- All new columns are additive and nullable (or have a SQL-level
  `DEFAULT`); migrations live in
  `_CHAT_CONVERSATION_MIGRATIONS` and `_CHAT_MESSAGE_MIGRATIONS`
  appended to `apps/api/services/db.py`, following the existing
  `_HERMES_TRACE_MIGRATIONS` pattern (idempotent + safe replay).
- Migrations have been verified on a copy of `data/slm_forge.db`
  before merge.

### R6 — No regressions
- Existing `chat_conversations` / `chat_messages` rows continue to
  load with the new columns NULL.
- Existing endpoints maintain their request/response shapes.
- The full pytest suite stays green; `apps/web` builds clean.

---

## 4. Interfaces (contract)

### 4.1 ORM additions

```python
# apps/api/models/chat.py (additions only — existing fields unchanged)
class ChatConversation(SQLModel, table=True):
    # ... existing fields ...
    tenant_id: str = Field(default="default", index=True)
    user_id: str | None = Field(default=None, index=True)
    summary_message_id: int | None = Field(default=None)  # FK to the
                                                          # latest summary row
    last_summarized_at: datetime | None = Field(default=None)

class ChatMessage(SQLModel, table=True):
    # ... existing fields ...
    # New role: "summary" — system-prepended summary of older turns.
    # Existing roles "user" | "assistant" | "system" | "tool" unchanged.
    tenant_id: str = Field(default="default", index=True)
    token_estimate: int = Field(default=0)  # cached len/4 for budget math
```

### 4.2 Migration list (additive, reversible)

```python
# apps/api/services/db.py
_CHAT_CONVERSATION_MIGRATIONS: list[tuple[str, str]] = [
    ("tenant_id",            "TEXT DEFAULT 'default'"),
    ("user_id",              "TEXT"),
    ("summary_message_id",   "INTEGER"),
    ("last_summarized_at",   "TIMESTAMP"),
]
_CHAT_MESSAGE_MIGRATIONS: list[tuple[str, str]] = [
    ("tenant_id",       "TEXT DEFAULT 'default'"),
    ("token_estimate",  "INTEGER DEFAULT 0"),
]
```

### 4.3 Pure history-composer (new module, no I/O)

```python
# packages/chat_agent/history.py
from langchain_core.messages import BaseMessage

KeepResult = tuple[list[BaseMessage], BaseMessage | None]
# (kept_messages, optional summary_message_to_prepend)

def compose_working_context(
    messages: list[BaseMessage],
    *,
    max_tokens: int,
    keep_last_n: int = 8,
) -> KeepResult:
    """Return the message list to feed the agent for the next turn.

    Strategy:
      1. Token-estimate the full history.
      2. If under budget → return (messages, None).
      3. Else, keep the last ``keep_last_n`` turns verbatim and ask
         Hermes to summarize the rest. Return (kept, SystemMessage(summary)).

    Pure: no DB, no HTTP. Caller injects the summarizer callable.
    """
```

### 4.4 Persisted-history loader (extended)

```python
# apps/api/routers/chat.py (or a sibling chat_history module)
def _load_history_as_langchain(db: Session, cid: int) -> list[BaseMessage]:
    """Replay ALL relevant role types in chronological order:

      * role="summary"   → SystemMessage (prepended once)
      * role="user"      → HumanMessage
      * role="assistant" → AIMessage(content, tool_calls=parsed_tool_calls_json)
      * role="tool"      → ToolMessage(content, tool_call_id=...)

    The order matches the original conversation timeline, so the
    LangGraph agent re-enters with full fidelity.
    """
```

### 4.5 Auth gate

```python
@router.get("/conversations/{cid}/messages", response_model=list[MessageOut])
@requires("read", "chat")
def list_messages(cid: int, request: Request, db: SessionDep): ...

@router.post("/conversations/{cid}/messages", response_model=MessageOut)
@requires("write", "chat")
def post_message(cid: int, payload: MessageCreate,
                 request: Request, db: SessionDep): ...

@router.get("/conversations/{cid}/stream")
@requires("read", "chat")  # streams are reads of the conversation
async def stream_conversation(cid: int, request: Request) -> EventSourceResponse: ...
```

Existing OPA policy under `policies/` gains a `chat.rego` package
(or extension to an existing one) implementing `allow_access`.

---

## 5. Constraints & non-functional requirements

- **Latency budget per turn:** No regression beyond +5% p95 on
  happy-path chats (history under budget); summarization-triggered
  turns may take +1× Hermes round trip (~2-5s) — acceptable because
  it only happens at the threshold crossing, not every turn.
- **Storage:** Bounded by the existing `chat_messages` table; the
  summary mechanism *replaces* historic tail messages from the
  agent's view but does NOT delete them from the DB (append-only
  audit trail).
- **Token-budget default:** `CHAT_HISTORY_MAX_TOKENS=24576` (75% of
  qwen3:30b-a3b 32k). Validated as an int on startup, fail-fast.
- **Idempotency:** Re-streaming the same conversation must not
  duplicate tool/summary messages. The persistence path uses the
  existing single-writer pattern (one assistant-turn row per
  `done` SSE event).
- **Backward compatibility:** Legacy rows missing the new columns
  load with safe defaults; legacy clients calling the existing
  endpoints with no auth still work in dev mode.

---

## 6. Acceptance criteria (DoD)

- **AC1.** On a new conversation, posting *"my name is Pat"* then
  *"what's my name?"* yields *"Pat"* — proven by an integration test
  that drives the SSE stream end-to-end against a fake LLM that just
  echoes the last `HumanMessage`'s last words.
- **AC2.** When turn N called a tool that returned a known fixture,
  on turn N+1 the agent's input message list contains the prior
  `AIMessage(tool_calls=...)` + `ToolMessage` — proven by a unit test
  asserting the messages passed to `graph.astream`.
- **AC3.** With `CHAT_HISTORY_MAX_TOKENS=200`, a conversation with
  20 turns produces (a) a `chat_messages` row of `role="summary"`
  and (b) an agent input of length ≤ `keep_last_n + 1` — proven by a
  unit test against `compose_working_context` and an integration
  test on the full stream.
- **AC4.** With `SLM_FORGE_AUTH_ENABLED=true` and a non-admin user
  who is not the conversation owner, `GET
  /conversations/{cid}/messages` returns 403 — proven by an API
  test.
- **AC5.** Two tenants (`tenant_id_ctx` bound to different values)
  see disjoint conversation lists from `GET /conversations` —
  proven by an API test.
- **AC6.** Each chat turn produces exactly one
  `hermes_traces.source = "chat:turn"` row with the turn's metadata
  — proven by an API test against the existing Traces endpoint.
- **AC7.** `uv run pytest -q` ≥ 358 passed (current 322 + new
  ≥ 36); `cd apps/web && npm run build` clean; `uv run mypy
  apps packages` introduces no new errors on touched modules.

---

## 7. Open questions (for the user before implementation)

1. **Per-user binding when auth is disabled.** In dev mode we have
   no real user; do you want conversations to be tied to the
   process-default user (`SLM_FORGE_DEFAULT_USER=anonymous`) so the
   isolation is visible from day one, or just keep them tenant-only?
   *Recommendation: tenant-only in disabled mode; user-bound when
   enabled.*
2. **Summarization model.** Use the same `CHAT_MODEL`
   (`qwen3:30b-a3b`) or a cheaper one (e.g. `qwen2.5:7b`) for
   summarization to cut latency? *Recommendation: same model, no
   second pull required.*
3. **Conversation deletion.** Out of scope for this spec, but if you
   want it later: append-only history → soft-delete via a
   `deleted_at` column rather than a DESTRUCTIVE `DELETE`.
