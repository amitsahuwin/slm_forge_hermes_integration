# ADR-0001 — Hermes / Ollama bridge: one chokepoint, retries, tenant boundary

- **Status:** Accepted
- **Date:** 2026-06-22
- **Workstream:** Hermes Hardening Ultraplan — Workstream 1, PR-1
- **Touches:** `packages/ratchet/hermes_bridge.py`, `apps/api/models/hermes_trace.py`, `apps/api/services/db.py`, `apps/api/services/tenant.py`, `packages/_log_context.py`

## Context

Before this change, `packages/ratchet/hermes_bridge.py` was the de facto
gateway between every SLM-Forge component (API routers, ratchet worker,
chat agent, dataset synth, agent runner) and the local Ollama LLM. But
the file violated four CLAUDE.md rules:

1. **Rule 16 (reliability).** `_call_ollama` did a single
   `httpx.post(..., timeout=300)`. One transient blip — Ollama
   restarting, a TCP RST, an upstream 503 — killed a multi-minute
   training-loop step. No retries, no backoff, no jitter.
2. **Rule 16 again (no silent fallbacks).** `propose_mutation`, when
   the LLM returned invalid JSON or a Pydantic-failing payload,
   fabricated a `MutationProposal(reasoning="LR halved as safe fallback")`
   whose hyperparam fields were all `None`. The reasoning string lied —
   no halving happened. Operators trusted the log and trained for hours
   without realising the autoresearch loop had silently degenerated.
3. **Rule 28 (logging hygiene).** A `log.info("Hermes raw response …")`
   call dumped up to 300 chars of every Ollama response into the JSON
   log stream, including dataset rows from synth + ingest pipelines.
4. **Rule 35 (multi-tenancy).** The `HermesTrace` table had no
   `tenant_id` column. A future multi-tenant deployment would require
   a non-trivial migration because all historical rows belong to no
   tenant.

## Decision

Keep the single-chokepoint design (one `_call_ollama` covers every
Ollama interaction) and harden it in place:

1. **Retries via `tenacity`.** Wrap `httpx.post` in a `Retrying`
   imperative loop with `stop_after_attempt(HERMES_MAX_RETRIES=3)`,
   `wait_exponential(min=0.5, max=4) + wait_random(0, 0.5)`. Retry
   ONLY on `ConnectError`, `ReadTimeout`, `RemoteProtocolError`, and
   HTTP `429 / 502 / 503 / 504`. Non-429 4xx codes never retry — they
   are deterministic user errors. The `HermesTrace` row records the
   final `attempts` count whether the call succeeded or exhausted.
2. **Typed failure for `propose_mutation`.** A new
   `MutationProposalError(RuntimeError)` is raised (chained from the
   underlying `JSONDecodeError` / `ValidationError`). The single caller
   — `packages/ratchet/loop.py:run_session` — catches it explicitly,
   records `mutation_reasoning="proposal_unparseable"`, and aborts the
   session after `HERMES_MAX_PROPOSAL_FAILURES=3` consecutive failures
   instead of silently no-op'ing.
3. **Targeted body redaction.** The inline raw-response log is gone.
   Replaced with a structured log carrying `len`, `sha256_prefix`,
   `duration_ms` — never the body. Trace persistence is gated by two
   env vars: `HERMES_TRACE_STORE_PAYLOADS` (global on/off, default on)
   and `HERMES_TRACE_REDACT_SOURCES` (comma-separated list of
   source labels whose bodies are blanked regardless). The default
   redact list covers `skill:dataset_synth`, `skill:ingest_dataset`,
   `skill:auto_label_unlabeled`, and `skill:data_quality_review` —
   every skill that routinely carries user dataset content.
4. **Tenant column from day one.** `HermesTrace` gets
   `tenant_id: str = Field(default="default", index=True)`. A new
   `apps/api/services/tenant.py` exposes `current_tenant()` which
   reads a contextvar set by `RequestContextMiddleware` in API context
   and falls through to `SLM_FORGE_TENANT_ID` / `SLM_FORGE_DEFAULT_TENANT`
   in worker context. `_record_trace` reads from `current_tenant()`,
   so single-tenant deployments need no code changes.

## Alternatives considered

- **Replace `tenacity` with a hand-rolled retry loop.** Considered for
  zero-dep purity, but `wait_exponential + wait_random` is already a
  five-line decorator with tenacity and a multi-line implementation
  by hand. The dep is small (~30 KB) and battle-tested.
- **Adopt `httpx`'s native `transport=` retry parameter.** Too coarse —
  it can't distinguish retryable HTTP statuses (429/5xx) from
  permanent 4xx, and it doesn't surface attempt counts to the
  `HermesTrace` row.
- **Add `tenant_id` as a NOT NULL column with no default.** Rejected
  because it would require manual ALTERs on existing prod databases.
  Additive forward-only migration with a `"default"` back-fill is the
  established repo pattern (see `_RUN_MIGRATIONS`, `_SESSION_MIGRATIONS`).

## Consequences

**Positive.**

- All four CLAUDE.md violations close in one PR.
- Single chokepoint preserves DRY: PR-2 / PR-3 / PR-4 all inherit
  retries, redaction, and tenant tagging for free via the new
  `run_skill` helper.
- Storm-cap behaviour visible to operators via the new `attempts`
  column on `HermesTrace`.

**Negative / trade-offs.**

- Worst-case latency expands. A 3-attempt × 300 s timeout × ~5 s
  backoff worst-case is ~915 s vs. the previous 300 s. Tune
  `HERMES_MAX_RETRIES=2` or `HERMES_OLLAMA_TIMEOUT_S=120` for
  production if this is intolerable.
- The contract change in `propose_mutation` is a breaking change for
  any out-of-tree caller that relied on the silent fallback. The only
  in-tree caller (`loop.run_session`) was updated atomically; out-of-
  tree callers will see `MutationProposalError` propagate.
- `HERMES_TRACE_REDACT_SOURCES` is opinionated by default. Developers
  debugging prompt regressions on the redacted skills must flip the
  env var locally or use the `sha256_prefix` log field to correlate.

## Verification

`tests/ratchet/test_hermes_bridge_retry.py` (5), `test_hermes_bridge_propose.py`
(4), `test_loop_proposal_failure.py` (3), `test_hermes_bridge_logging.py`
(6), `tests/api/test_hermes_trace_tenant.py` (7),
`tests/api/test_db_migration_hermes_trace.py` (3). 28 tests total,
all green at commit time.
