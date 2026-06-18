# CLAUDE.md — Engineering Guidelines

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

---
