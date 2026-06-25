# ADR-0004 — API startup never auto-resumes user work

- **Status:** Accepted
- **Date:** 2026-06-25
- **Supersedes:** the original Phase R "re-queue stranded work" startup policy
- **Related:** ADR-0001 (Hermes bridge chokepoint), `docs/specs/PHASE_R_SPEC.md`

## Context

The Phase R lifespan hook (`_recover_stranded_runs_and_sessions` in
`apps/api/main.py`) was designed to recover work after a server crash:
every time the API booted, it scanned for `Run`s and `TrainingSession`s
left in `running` state and flipped them back to `queued` so workers
would pick them up again.

Two problems surfaced in practice:

1. **It auto-restarted finished experiments.** A `TrainingSession` ends
   up at `status="running"` even when its work is done — for example
   when ratchet's final `patch_session(status="completed")` PATCH at
   `packages/ratchet/loop.py:293` fails (network blip, container
   restart between the last run completing and the status flip), or
   when the ratchet host process is killed after writing all child
   runs as `completed`. The recovery code had no way to tell
   "done-but-unflipped" from "genuinely orphaned" — it requeued both.
2. **It hammered Ollama on every boot.** The ratchet worker
   (`packages/ratchet/__main__.py`) polls `status=queued` sessions; a
   re-queued session immediately triggered fresh Hermes mutation
   proposals for every round, generating Ollama load that the user
   never asked for.

This was reported as a UX bug: "even after the experiment is completed
successfully why is it Re-queued by API startup recover?? Ideally the
user must have an option to rerun the experiment/run, it must not
retrigger automatically."

## Decision

API startup never auto-resumes user work. Stranded state is
reconciled-or-failed, not requeued. The user reruns explicitly.

Concretely:

- **`TrainingSession` recovery** (`_recover_stranded` in
  `apps/api/main.py`):
  - If any child run is `COMPLETED` → session is reconciled to
    `COMPLETED` (preserve existing `best_run_id`; derive one from the
    lowest `final_val_loss` if absent). The session keeps its result;
    no error banner.
  - Else if all child runs are terminal (`FAILED`/`CANCELLED`) →
    session goes to `FAILED` with "All training runs failed before the
    experiment could complete. Click Rerun to try again."
  - Else (no children, or any child still non-terminal) → session goes
    to `FAILED` with "Server restarted while this experiment was in
    progress. Rerun it manually if you want to continue."
  - No path ever sets a session to `QUEUED` on boot.

- **Run-level recovery** (`release_expired_claims` in
  `apps/api/services/claims.py`):
  - New parameter `stranded_action: Literal["requeue", "fail"]`,
    default `"requeue"`.
  - Startup sweep (`include_legacy=True, stranded_action="fail"`):
    expired/legacy `running` runs go to `FAILED`, not `QUEUED`.
  - Mid-operation sweep called from `claim_next_run` (default
    `"requeue"`): unchanged — a dead worker's stale claim still goes
    back into the pool for a living worker to pick up. This is correct
    because the API is alive and the user did intend the work to run.

## Consequences

- **Pro — Ollama is quiet on boot.** Restarting the API no longer
  triggers a wave of Hermes proposals; the ratchet worker only runs
  what the user explicitly queues.
- **Pro — completed experiments stop showing a red "Re-queued"
  banner.** The user keeps their `best_run_id`/`best_metric_value`.
- **Pro — the policy is one sentence:** "the API never auto-resumes
  user work on boot." Easy to reason about; one terminal write per
  stranded row.
- **Con — genuinely interrupted experiments need an explicit rerun.**
  This is the user's stated preference and the contract this ADR
  formalises. The follow-up to this ADR (Phase 2 of the same plan) is
  an explicit `POST /api/v1/sessions/{id}/rerun` endpoint + UI button
  on `ExperimentDetail`, which clones the session config and queues a
  fresh experiment. Tracked separately.

## Alternatives considered

1. **Lease/heartbeat-aware session recovery (parallel to runs).** Add
   a session-level heartbeat from the ratchet worker; on startup, only
   requeue sessions whose heartbeat is stale. Rejected: still
   auto-resumes work without consent, doesn't address the user's "I
   want to choose to rerun" requirement, and adds DB schema for a
   policy that has a simpler answer.
2. **Just check child-run terminality before requeueing.** Would fix
   the "completed-but-stuck-at-running" case but still auto-resumes
   genuinely-orphaned experiments. Rejected for the same reason —
   contradicts the explicit user requirement.
3. **Do nothing on boot — leave stranded rows as-is.** Rejected: the
   UI surfaces `status="running"` with no driver behind it, which is
   misleading. The new terminal state with a "Rerun manually" hint
   gives the user something to act on.

## Implementation

- `apps/api/services/claims.py` — `release_expired_claims` gains
  `stranded_action` parameter.
- `apps/api/main.py` — `_recover_stranded(db) -> (runs_failed,
  sessions_touched)` extracted as a testable pure-DB function;
  `_recover_stranded_runs_and_sessions` is now just the lifespan
  wrapper.
- Tests: `tests/api/test_startup_recovery.py` (new, 11 cases);
  `tests/api/test_run_claiming.py` legacy contract preserved.