# ADR-0003 — LiteLLM proxy fronts host Ollama for the auto-fix SDK

- **Status:** Accepted
- **Date:** 2026-06-25
- **Supersedes:** none
- **Related:** ADR-0002 (self-healing error reporter)

## Context

The dev-mode auto-fix loop (ADR-0002 § PR-B) calls `claude_agent_sdk` to
propose a fix for an uncaught exception. The SDK only speaks the **Anthropic
Messages API** (`POST /v1/messages`). Our local stack relies on **Ollama**
for every other LLM workload (Hermes skills, ratchet mutation proposals, chat,
synth). The two protocols are incompatible.

The user requirement: the auto-fix loop must run against a local model
(`qwen3:30b-a3b` served by Ollama), not the real Anthropic Cloud. Reasons:

1. Cost — repeated fix attempts at sandbox-resolution would burn through
   real Anthropic credits during regular dev.
2. Latency / sovereignty — failure tracebacks include redacted-but-still-
   sensitive runtime context; the lab is designed to stay local-first.
3. Consistency — every other LLM call already routes through host Ollama.

## Decision

Run a **LiteLLM proxy** as a Docker-compose service (`autofix` profile,
opt-in via `make litellm-up`). LiteLLM exposes an Anthropic-compatible
endpoint at `:4000` and translates incoming `POST /v1/messages` requests
into Ollama HTTP calls against `host.docker.internal:11434`.

Aliases in `litellm/config.yaml` map Anthropic-shape names
(`anthropic/claude-3-5-sonnet-20241022`, `anthropic/qwen3-30b-a3b`) to the
underlying `ollama_chat/qwen3:30b-a3b`. The SDK picks one via the new
`AUTOFIX_MODEL` env var, which the orchestrator forwards into
`ClaudeAgentOptions(model=...)` (see `packages/error_responder/sdk_client.py:_resolve_model`).

### Wiring

| Context | `ANTHROPIC_BASE_URL` | `AUTOFIX_MODEL` |
|---|---|---|
| API container (docker-compose) | `http://litellm:4000` (compose DNS) | from env, default `anthropic/claude-3-5-sonnet-20241022` |
| Host workers (trainer/ratchet/exporter) | `http://localhost:4000` (port-mapped) | from `.env` |

`ANTHROPIC_API_KEY` and `LITELLM_MASTER_KEY` are the same shared secret;
both default to `sk-local-litellm-master` so a fresh checkout runs without
key juggling. Production deployments rotate this via the env.

### Hermes is untouched

The Hermes path (ratchet + skills + chat + post-mortem + remedy + QA)
continues to call Ollama directly via `OLLAMA_URL` and `HERMES_MODEL`.
Only the auto-fix SDK path is routed through LiteLLM.

## Alternatives considered

1. **Force real Anthropic Cloud.** Rejected — burns budget on dev-time
   exception storms, and the lab is meant to operate offline.
2. **Implement an Anthropic-shim ourselves.** Rejected — protocol surface
   (streaming, tool calls, system messages, prompt caching) is non-trivial
   and would diverge from upstream over time. LiteLLM has community
   maintenance + multi-model routing for free.
3. **In-process `litellm` Python adapter (no docker service).** Plausible
   for unit tests, but adds an unavoidable import + venv coupling for every
   worker. A standalone proxy keeps the boundary clean.

## Consequences

**Pros**
- One Anthropic-shaped target for the SDK; one Ollama instance for the rest.
- Swapping the underlying model is one line in `litellm/config.yaml`.
- The proxy is opt-in (profile-gated); default `make dev` is unchanged.
- Real-Anthropic deployments are a config flip away — point
  `ANTHROPIC_BASE_URL=https://api.anthropic.com` and set the real key.

**Cons**
- One more container to manage and keep up to date.
- Local Ollama models may not match Claude's instruction-following fidelity
  for code edits — empirical regressions in fix quality should be expected
  and tracked via the AutoFixes admin tab (verified vs failed ratio).

## Verification

1. `make litellm-up` → `curl http://localhost:4000/v1/models` should list
   the configured aliases.
2. `curl -H "Authorization: Bearer $LITELLM_MASTER_KEY"
   http://localhost:4000/v1/messages -d '{"model":"anthropic/qwen3-30b-a3b",
   "messages":[{"role":"user","content":"ping"}], "max_tokens":16}'`
   returns a normal Anthropic-shaped response.
3. Set `AUTOFIX_ENABLED=true`, induce a controlled crash, observe the
   `auto-fix/<fp>` branch + PR appear (see `release/0.7.1.md` for the
   full verification recipe).
