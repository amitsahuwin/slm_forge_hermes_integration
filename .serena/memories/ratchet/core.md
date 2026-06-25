# packages/ratchet — autoresearch loop

Orchestrates a `TrainingSession` as a sequence of `Run`s. Each round asks Hermes for a hyperparameter mutation, POSTs a child Run, waits, accepts/rejects on val-loss.

Entrypoint: `python -m packages.ratchet` (`make ratchet`). **Requires Ollama on `:11434`.**

## Layout
- `__main__.py` — worker `main()`; polls API for queued sessions.
- `loop.py` — `run_session(...)` / `_run_session_inner(...)` / `_wait_for_run(...)`. The session-level fields (`base_model`, `trainer_backend`) **must** be threaded onto each child Run in the `run_payload` here — otherwise child runs inherit model defaults.
- `hermes_bridge.py` — `propose_mutation()` (mutation suggestion via Hermes/Ollama), `load_skill()`, `_call_ollama()`, `run_skill()`, `_record_trace()`, `healthcheck()`, `MutationProposal`, `MutationProposalError`.
- `heartbeat.py` — `start_heartbeat()` (worker liveness).

## Important
- Hermes calls go via Ollama HTTP API (`:11434`); skill definitions loaded from `.hermes-skills/`.
- Single proposal failure must skip the iteration, not crash the loop (see `tests/ratchet/test_loop_proposal_failure.py`).
- Mutation proposals are traced via `HermesTrace` rows for auditability.
