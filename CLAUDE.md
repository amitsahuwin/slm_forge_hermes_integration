# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SLM-Forge is a local-first lab for fine-tuning small language models. A FastAPI + SQLite backend and a React UI run in Docker; the GPU-bound workers (trainer / ratchet / exporter) run on the **host**. Training is multi-backend: MLX on Apple Silicon, PEFT + TRL on NVIDIA CUDA. `README.md` is the canonical product entry point; `docs/specs/PHASE_*_SPEC.md` are the per-phase specs and `docs/PLAN.md` is the build log.

## Commands

Python is managed by **`uv`** (never call `pip`/`python` directly — use `uv run …`); the web app uses `npm`. `make help` lists every target; `make platform-info` shows the detected host + chosen trainer backend.

```bash
# Setup (auto-detects macOS/MLX vs Linux/CUDA; provisions managed Python 3.12)
make setup
make seed-data                       # copy bundled sample datasets into data/datasets/

# Core stack — UI on :5173, API on :8000 (Docker)
make dev                             # foreground (make dev-d for detached)

# Host workers — each in its own terminal (NOT in Docker; need GPU/Metal)
make trainer                         # auto backend; or make trainer-mlx / make trainer-cuda
make ratchet                         # autoresearch loop (requires Ollama on :11434)
make exporter                        # GGUF export (requires llama.cpp tooling)

# Python tests / lint / types
uv run pytest -q                                   # full suite (testpaths=tests, asyncio auto)
uv run pytest tests/api/test_run_validation.py     # one file
uv run pytest tests/api/test_run_validation.py::test_broken_model_is_422   # one test
uv run ruff check --fix <paths>                    # line-length 100; lint only your changed
                                                   # files — the repo has many pre-existing findings
uv run mypy apps packages

# Web app (apps/web)
cd apps/web && npm run build         # tsc --noEmit && vite build  ← the type gate
cd apps/web && npm run dev           # Vite dev server

# Policy + auth
make opa-test                        # Rego unit tests (policies/)
make auth ENABLED=true|false         # Keycloak + OPA up, enforcement on/off
make auth-token                      # mint a JWT for admin@local for curl testing
```

## Architecture

**Layout.** `apps/api` (FastAPI + SQLModel + sse-starlette over SQLite), `apps/web` (React 19 + Vite + Tailwind + react-router 7), `packages/*` (host workers + Hermes agents, ingest, synth, research). `policies/` holds OPA Rego; `docs/` holds specs/runbooks.

**Run vs. Session.** A `Run` (`apps/api/models/run.py`) is one fine-tuning job. A `TrainingSession` (`models/session.py`) is an autoresearch *experiment* — a sequence of Runs. The ratchet worker (`packages/ratchet/loop.py`) orchestrates a session: each round it asks Hermes for a hyperparameter mutation, `POST`s a child Run, waits for it, and accepts/rejects on val-loss. Session-level fields (e.g. `base_model`, `trainer_backend`) must be threaded onto each child Run in the loop's `run_payload`, or the runs inherit model defaults.

**Backend-aware claim queue.** Both Runs and Sessions carry `trainer_backend` (`"mlx"` | `"cuda"`). Workers don't poll-and-pick; they `POST /api/v1/runs/claim` filtered by their backend (atomic compare-and-swap + lease recovery), so a Mac and a remote A100 can share one API with **no shared filesystem** — datasets download and adapters upload over HTTP. A run queued for a backend no worker is running stays `queued` forever; that's the usual cause of "nothing happens."

**Pluggable trainer.** `packages/trainer/backends/` registers backends behind `TrainerBackend`: `mlx.py` shells out to `mlx_lm.lora`, `cuda.py` → `cuda_train.py` (PEFT + TRL + bitsandbytes). `runner.py` runs the subprocess, parses its stdout into normalized `TrainEvent`s, and POSTs them as metrics. Workers inherit `os.environ` into the subprocess, and the worker entrypoints load `.env` (e.g. `HF_TOKEN` for gated HF repos) via the guarded `load_dotenv` pattern.

**Model catalog.** `apps/api/services/model_catalog.py` maps one logical model → per-backend physical checkpoints (MLX 4-bit vs full-precision CUDA) with memory/status/`gated` metadata. `validate_run_request(base_model, trainer_backend)` enforces it at Run *and* Session creation (422 on a bad/broken/mismatched combo); bypass with `SLM_FORGE_ENFORCE_CATALOG=false`. The frontend drives its model dropdowns from `/api/v1/models/v2` filtered by the selected backend.

**Migrations.** SQLite schema is created by SQLModel `create_all`; additive forward-migrations live in `apps/api/services/db.py` (`_RUN_MIGRATIONS`, `_SESSION_MIGRATIONS` → `init_db()`). Add a column there with a default rather than hand-editing tables.

**Hermes / Ollama.** `qwen3:30b-a3b` via Ollama powers the skill endpoints, ratchet mutation proposals, chat agents, and R&D research grounding. **Auth** is Keycloak (JWT/SSO) + OPA (Rego), with a service-token bypass for host workers; off by default. **Observability**: `SLM_FORGE_LOG_FORMAT=json` worker logs → Promtail → Loki → Grafana, plus Prometheus `/metrics` (`make obs-up`). An **MCP server** (`make mcp-up`, :8765) exposes the lab to Claude Desktop / Cursor / Claude Code.

> Note on this repo's own conventions: specs live in `docs/specs/` (not `docs/spec/`), and the commit message is written to `commit_message.md` (gitignored).

---

## Engineering guidelines

Rules for every task in this repo. Build like a **staff/principal engineer**: correct, secure, readable, no shortcuts. The **Definition of Done** is the gate every change must pass.

## Principles
- Build for today's real requirements with clean seams; don't build speculatively. When "future-proof" clashes with YAGNI, YAGNI wins unless there's a concrete near-term need.
- Correctness, security, and readability over cleverness.
- Measure before optimizing; never add complexity (concurrency, caching) on a hunch.
- Fail fast: validate inputs/config at startup, surface errors early.
- Ask when the spec is ambiguous.
- Always use the configured context tooling (Graphify; MCPs: Context7, Serena, Headroom) to load only relevant context and cut token cost.

## Plan & Spec (spec-driven)
1. Spec first, per phase, in `docs/spec/` — scope, I/O, data models, interfaces, constraints, non-goals. No code until the phase spec is clear.
2. Phased plan in `docs/plans/` with a dated, descriptive filename. Spec = *what*; plan = *how/when*.
3. **Architect to MAANG-scale standards**: the plan's architecture must be robust, well-integrated, low-latency, and future-proof — handling high load and scaling elastically up *and* down on demand (stateless services, horizontal scale, async/queues, caching, clear boundaries). Justify it against expected load and growth, not just the happy path.
4. Red-team the plan as devil's advocate (flaws, coupling, scaling, security, failure modes); revise and repeat — ≥3 passes, continuing until one pass is clean, without manufacturing nitpicks.
5. Define DoD + acceptance criteria before starting; "done" = criteria demonstrably met.
6. On requirement change, update in order: spec → tests → code. Spec is the source of truth.

## TDD & Test Quality
7. Write failing tests first, then implement to green (red → green → refactor).
8. Run the full suite yourself; fix root causes until green. Never ship unexecuted code.
9. Tests are a contract: never delete or weaken them to pass — they change only when the spec changes, only to reflect correct behavior.
10. Meaningful coverage ≥90% (a floor, not the goal): assert real behavior, edge cases, failure paths. Use unit/integration/e2e/contract as applicable.

## Code & Architecture
11. DRY + YAGNI: extract shared logic, but don't abstract before ~2–3 real call sites.
12. SOLID; depend on abstractions; composition over inheritance.
13. Use the right design pattern for the problem — no pattern soup.
14. Types, lint, format, and type-check all on; treat lint/type errors as build failures.
15. **No versioned code modules** (`*_v1`, `*_v2`): change in place to avoid scattered refactors. (Versioning MD files is fine.)
16. Error handling & reliability: validate inputs, timeouts, retries w/ backoff+jitter, circuit breakers, idempotency, graceful degradation; never swallow errors; **no silent fallback defaults**. Contain and surface failures.

## Performance & Concurrency
17. Use async/threads/multiprocessing/parallelism only where profiling shows benefit; guard all shared state (races/deadlocks/ordering). CPU-bound Python → multiprocessing/native, not threads (GIL).
18. Optimize with evidence: load-test and profile; add caching/batching/pooling/indexing against measured bottlenecks.

## Security (AAA + beyond)
19. AAA, no compromise — Authentication (MFA/secure sessions, short-lived tokens), Authorization (least-privilege, enforced server-side), Accounting (tamper-evident audit log).
20. OWASP Top 10: validate/sanitize input, encode output, parameterized queries, CSRF/CORS, rate-limiting, encrypt in transit + at rest.
21. CI scans: SCA, SAST, secret, and image scanning; fail the build on high-severity findings.

## Config & Secrets
22. Never hardcode secrets or env-specific values. `.env` for local dev only; production secrets in a managed store. Commit `.env.example` (no real values); keep `.env` gitignored.
23. Validate config at startup and fail fast; no silent defaults for security/behavior-critical settings. Follow 12-Factor.

## Data
24. Per project, choose SQL vs NoSQL (or both, per use case) on consistency/scale/query fit — Postgres a strong default; MySQL/MariaDB are valid production DBs; avoid SQLite for high-concurrency/multi-tenant (fine for embedded/edge/test).
25. **Never use local disk as the datastore — always a DB.** Large binaries → object storage (S3/blob) with references kept in the DB.
26. Schema via versioned, reversible, backward-compatible migrations (expand → migrate → contract); never hand-edit prod schema or run destructive migrations without a verified backup.
27. Connection pooling, query-driven indexing, backups with tested restores, DR/rollback plan.

## Observability & Ops
28. Structured (JSON) logs with correlation IDs; never log secrets/PII.
29. Expose app/business metrics + distributed tracing.
30. Health/readiness/liveness endpoints; graceful shutdown (drain in-flight work); deterministic startup.
31. CI/CD: build → lint → type → test → scan → deploy; green to ship. Decouple deploy/release with feature flags.

## Dependencies & Runtime
32. Use **`uv`**, not pip.
33. Recent but pinned via lockfile; "latest" isn't auto-safe — review changelogs, update deliberately.
34. Harden containers: minimal/trusted images, non-root, pinned digests, scanned, small; manage infra as code.

## Multi-Tenancy & Scale
35. Thread a tenant boundary through data/queries/caches/logs from day one so multi-tenancy needs no rewrite; don't build per-tenant infra/billing/UI speculatively. **Data isolation is non-negotiable** — no cross-tenant access.
36. Scale horizontally: stateless services, externalized state, load-balanced.

## Git, Releases & Handover
37. Write the commit message to `commit_message.md` first (Conventional Commits, *what* + *why*), then `git add .` → `git commit -F commit_message.md` → push. Gitignore `commit_message.md`; never force-push shared branches or commit secrets; push to the correct branch (feature branch + PR where review applies).
38. Maintain `./release/` notes per release — Keep-a-Changelog + SemVer, with impact and migration steps.
39. After shipping, give the user: a short change summary (*what changed*), the release-notes file link, and how to verify it — UI click steps and/or runnable `curl` examples.

## Docs
40. Keep `README.md` and `Makefile` current; expose `make` targets for setup/build/test/lint/run/migrate/deploy. Any architectural or script change updates the README.
41. Record decisions as ADRs (`docs/adr/`); comment the *why*, not the *what*.

## Definition of Done (gate)
- [ ] Spec (`docs/spec/`) + plan (`docs/plans/`) with a robust, scalable, low-latency, elastic architecture; ≥3 clean red-team passes; acceptance criteria met.
- [ ] Tests written first, all green, meaningful coverage ≥90%.
- [ ] No hardcoded values/secrets; config env-driven and validated; no `*_v#` code modules.
- [ ] AAA enforced; SCA/SAST/secret/image scans clean; errors handled; logs/metrics/health in place; no secrets/PII logged.
- [ ] DRY/YAGNI; lint/format/type-check clean; data in a DB (not disk); migrations reversible + backed up; tenant isolation intact.
- [ ] `README`/`Makefile`/ADRs/`./release/` updated; commit via `commit_message.md`.
- [ ] Change summary + release link + UI/`curl` verification steps delivered to the user.

---

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
