# ADR-0002 — Self-healing error reporter: capture → redact → dispatch

- **Status:** Accepted
- **Date:** 2026-06-22
- **Workstream:** Hermes Hardening Ultraplan — Workstream 2, PR-A + PR-B
- **Touches:** new `packages/error_responder/` package; new
  `apps/api/middleware/error_capture.py`, `apps/api/models/autofix.py`,
  `apps/api/routers/autofix.py`; lifespan + middleware wires in
  `apps/api/main.py`; worker entrypoint wrappers in
  `packages/{trainer,ratchet,exporter}/__main__.py`.

## Context

Before this change, an uncaught exception inside the API or a worker
landed in the JSON log stream and that was it. Operators had to grep
logs to find recurring bugs, dedupe by hand, and either file GitHub
issues manually or kick off a debugging session from scratch. In a
local-first deployment with no SaaS Sentry equivalent available, errors
disappeared into log files.

We wanted three things simultaneously:

1. **Production:** every uncaught exception opens a deduplicated
   GitHub issue automatically, with redacted traceback + correlation
   IDs + occurrence count.
2. **Development:** the same captured error feeds an LLM auto-fix
   loop that proposes a fix + a reproducing test, runs the test
   twice (must FAIL before, PASS after), and commits — but never
   to `main`.
3. **Safety:** no exception inside the responder itself can bring
   down the API or a worker; secrets never reach GitHub or the SDK
   prompt; the auto-fix loop refuses to touch infrastructure files.

## Decision

**Architecture.** A single new top-level package
`packages/error_responder/` owns the entire flow. Public surface:
`capture.report_exception` (async), `capture.report_exception_sync`
(workers), `capture.flush`. Internals are kept thin and
single-purpose:

- `config.py` — frozen `ErrorReporterSettings` singleton, validated at
  startup (fail-fast on missing `GITHUB_TOKEN` in production, missing
  Anthropic creds when `AUTOFIX_ENABLED=true`, etc.).
- `fingerprint.py` — line-number-insensitive SHA-256 over
  `exception_type | top-3 project frames`. Six conservative secret-
  redaction patterns (Bearer, `api_key=`/`password=`/`token=`/`secret=`,
  AKIA, `sk-(ant|live|proj)-*`, JWT, email).
- `reporter.py` — sync + async capture entry points; bounded
  `asyncio.Queue` consumed by a dispatcher task started in the API
  `lifespan`. Sliding 60-second storm window per fingerprint
  suppresses GitHub spam once the per-fp count exceeds
  `ERROR_REPORTER_STORM_THRESHOLD` (default 10).
- `github_issue.py` — `httpx` POST to api.github.com (no PyGithub).
  Dedup via the GitHub search API: a `sha256:<12>` anchor in the body
  lets us comment on an existing open issue instead of opening a
  duplicate.
- `autofix.py` — dev-mode orchestrator (10-gate preflight → sandbox
  branch → `claude_agent_sdk` → test-quality gate → full pytest →
  commit). Lock via `fcntl.flock` on `/tmp/slm_forge_autofix.lock` for
  single-in-flight.
- `sdk_client.py` — thin `claude_agent_sdk` wrapper with prompt
  template + JSON manifest parser; lazy-imported so production code
  pays no SDK import cost.

**Hook points (four).**
1. `@app.exception_handler(Exception)` in `apps/api/main.py` — catches
   anything that escapes a route handler. 4xx `HTTPException` flows
   through FastAPI's default; 5xx is reported.
2. New `ErrorCaptureMiddleware` mounted just inside CORS — catches
   exceptions that bypass the route handler (auth middleware, JWKS
   failures, Prometheus accounting bugs).
3. `loop.set_exception_handler` in the API `lifespan` — captures
   silent `asyncio.create_task` failures (e.g. background QA scans).
4. `try/except BaseException` wrappers at the bottom of each worker
   `__main__.py`. Re-raises after `capture + flush` so the exit code
   and stderr trace are preserved. `KeyboardInterrupt` and
   `SystemExit` are explicitly NOT reported — they're intentional exits.

**Dev-mode deploy choice: `auto-commit-reload`.** Three options were
on the table:
- **auto-merge to main** — fastest feedback, but LLM-generated code
  lands on the main branch with no human review.
- **pr** — safest, but the fix doesn't take effect until a human
  clicks Merge. Defeats "self-healing."
- **auto-commit-reload (chosen)** — commit on the sandbox branch
  `auto-fix/<fp12>-<utcstamp>`. uvicorn `--reload` picks up the
  source-file change via watchfiles automatically; main HEAD never
  moves. Workers emit `autofix.restart_required` and an operator
  restarts them (workers are host-bound; auto-killing a trainer
  mid-run is unacceptable).

User picked option 3 explicitly during planning (`docs/ultra_plan_Hermes_hardning.md`
"User decisions locked in"). It's the right balance between
"self-healing" and "main is sacred."

**Mandatory safeguards.** Ten preflight gates, each ANDed; any False
short-circuits to `status=rejected` and falls through to the
GitHub-issue path:

1. `AUTOFIX_ENABLED=true`
2. `claude_agent_sdk` importable
3. `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY` set
4. Git repo present
5. Current branch is NOT `main`
6. Working tree is clean (`git status --porcelain` empty)
7. File-lock at `/tmp/slm_forge_autofix.lock` acquired
8. Target file path NOT in `AUTOFIX_DENYLIST`
9. Target file path NOT under `tests/`
10. Target file does NOT contain a `# NO_AUTOFIX` directive
11. `attempts_in_24h(fingerprint) < AUTOFIX_MAX_ATTEMPTS_PER_FINGERPRINT_24H`

Plus a non-negotiable test-quality gate: `git stash` the source-file
changes (leave the new test in place) and rerun the test — it MUST
FAIL. Pop the stash; rerun — it MUST PASS. Tautological tests that
pass with or without the fix are rejected.

## Alternatives considered

- **Use Sentry SDK + the official Sentry GitHub integration.**
  Rejected: this is a local-first deployment with no Sentry instance
  available. The whole point is no SaaS dependency on the error
  pipeline.
- **Use PyGithub instead of raw httpx.** Rejected: ~6 MB of transitive
  deps for two endpoints we need (`POST /issues`, `GET /search/issues`).
  Native httpx wins on dependency hygiene.
- **Run the SDK auto-fix loop in production.** Hard rejected. Auto-
  applying LLM-generated code to a production codebase without human
  review is a documented anti-pattern. Production records the error
  and opens a GitHub issue; humans decide what to do.
- **Skip the test-quality gate.** Rejected — a tautological test
  (`def test_x(): assert 1 == 1`) passes immediately and gives the
  loop a false-positive `status=deployed`. The gate adds ~5 s and
  catches this exact failure mode.

## Consequences

**Positive.**

- Every uncaught exception is now observable end-to-end without log
  spelunking.
- Production teams get GitHub issues with redacted tracebacks +
  correlation IDs — no log scraping required.
- Dev teams can opt into auto-fix and see "real fix proposed on
  branch X" instead of "failed locally."
- The new admin UI (`/autofix`) makes the audit trail a normal part
  of the workflow.

**Negative / trade-offs.**

- New runtime dependency: `claude-agent-sdk>=0.2.106` (optional extra
  `error-responder`). Install via `uv sync --extra error-responder`.
- Anthropic API cost in dev mode. Bounded by
  `ERROR_REPORTER_SDK_HOURLY_CAP=100` (hard cap) +
  `AUTOFIX_MAX_ATTEMPTS_PER_FINGERPRINT_24H=3` (per-fingerprint cap).
- Storm-cap suppression means the GitHub issue thread for a
  high-frequency error class won't capture every occurrence — only
  the first 10 in any 60s window. The local `AutoFixAttempt` table
  still records every one with `status=skipped` so we don't lose
  visibility.
- The reporter package is in `AUTOFIX_DENYLIST` by default —
  auto-fix is not allowed to self-modify, which is the correct
  safety stance but means bugs in the reporter itself need a human.

## Verification

`tests/error_responder/test_fingerprint.py` (18),
`test_config.py` (11), `test_reporter.py` (6),
`test_autofix.py` (17). 52 tests total, all green at commit time.

End-to-end demo:
```bash
DEPLOYMENT_MODE=development AUTOFIX_ENABLED=true \
  ANTHROPIC_API_KEY=... \
  make dev
# induce a NameError in a non-denylisted file
# observe `auto-fix/<fp>` branch + AutoFixAttempt row at /autofix
git rev-parse main  # unchanged
```
