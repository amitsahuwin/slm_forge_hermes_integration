# Conventions (codebase-specific)

## Repo-specific quirks (vs. global CLAUDE.md)
- Specs live in **`docs/specs/`** (plural — not `docs/spec/`).
- Commit message → **`commit_message.md`** (gitignored) → `git add . && git commit -F commit_message.md`. Conventional Commits, *what + why*.
- README is the canonical product entry point; `docs/specs/PHASE_*_SPEC.md` are per-phase specs; `docs/PLAN.md` is the build log.
- Release notes per release in `release/` (Keep-a-Changelog + SemVer).

## Hard rules (Definition of Done gate)
- **No versioned code modules** (`*_v1`, `*_v2`) — change in place. Versioning MD files is fine.
- **No silent fallback defaults** — validate inputs/config at startup and fail fast.
- **No hardcoded secrets/env-specific values** — `.env` local-only (gitignored), `.env.example` committed.
- **DB is the source of truth** — never use local disk as the datastore. Large binaries → object storage with refs in DB. (Adapters/exports on disk are referenced from DB rows.)
- **Migrations are forward-additive** — add columns in `apps/api/services/db.py` (`_RUN_MIGRATIONS`, `_SESSION_MIGRATIONS`) with defaults, never hand-edit tables.
- **Tenant isolation is non-negotiable** — thread tenant boundary through data/queries/caches/logs (see `apps/api/services/tenant.py:current_tenant`).
- **Tests are a contract** — never delete/weaken to pass; they change only when the spec changes. Write failing tests first.
- **Coverage floor ≥90%** (meaningful — real behavior, edge cases, failure paths).
- **AAA, no compromise** — Authentication, Authorization (server-side, least-privilege), Accounting (tamper-evident audit log).

## Code style
- Python: ruff line-length 100, target py312, lint set `E,F,I,N,UP,B,A,C4,SIM,RUF`, ignore `E501`. **Lint only your changed files** — repo has many pre-existing findings.
- mypy strict on `apps packages` (`ignore_missing_imports = true`).
- Lint/type errors are build failures.
- Comment the *why*, not the *what*. Record decisions as ADRs in `docs/adr/`.

## Architecture rules
- DRY+YAGNI — don't abstract before ~2-3 real call sites; YAGNI wins over speculative future-proofing unless concrete near-term need.
- SOLID, depend on abstractions, composition over inheritance.
- Error handling: timeouts, retries with backoff+jitter, circuit breakers, idempotency, graceful degradation. Never swallow errors.
- Concurrency only with profiling evidence (CPU-bound Python → multiprocessing/native, not threads — GIL).
- Stateless services, horizontal scale, externalized state. Multi-tenancy from day one — no per-tenant infra/billing/UI speculatively.

## Logging / observability
- Structured JSON logs with correlation IDs via `packages/_logging.py` + `packages/_log_context.py` (`bind()` / `reset()` / `binding()`).
- Worker entrypoints set `SLM_FORGE_LOG_FORMAT=json` (auto in Makefile).
- Never log secrets/PII.
- App + business metrics via Prometheus (`/metrics`); distributed tracing where applicable.
- Health/readiness/liveness endpoints; graceful shutdown drains in-flight work.

## Spec-driven workflow (per task)
1. Spec first (`docs/specs/`) — scope, I/O, data models, interfaces, constraints, non-goals.
2. Phased plan (`docs/plans/<dated-filename>.md`).
3. ≥3 red-team passes on architecture before code.
4. Acceptance criteria + DoD defined up front.
5. On requirement change: spec → tests → code.

## Worker env
- Workers inherit `os.environ` into trainer subprocess; entrypoints load `.env` via guarded `load_dotenv` (so `HF_TOKEN` etc. reach the subprocess).
- `make trainer` exports `SLM_FORGE_TRAINER_BACKEND=mlx|cuda` and `SLM_FORGE_LOG_FORMAT=json`.

## Run validation
- `validate_run_request(base_model, trainer_backend)` enforces catalog at both Run *and* Session creation (422 on broken/mismatched). Bypass dev-only: `SLM_FORGE_ENFORCE_CATALOG=false`.

## Always do
- `uv` not pip; `uv run …` for any python invocation.
- `make platform-info` when unsure about backend selection.
- Refresh `graphify-out/` with `graphify update .` after code changes.
