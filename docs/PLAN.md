# SLM-Forge — Enhancement Plan (Phases I → N)

Author: planning round for Amit. Date: 2026-06-08.

This plan covers seven asks that came in together. Each ask is a focused phase with deliverables, file targets, design trade-offs called out, and an effort estimate. Nothing here is implemented yet — approval requested before each phase.

Phases A–H are already shipped and verified (live logs, dashboard rebuild, exports/datasets fixes, chat, canary drift, mask_prompt fix, capabilities heartbeat, ingest converter, synthesize). This plan is everything new.

---

## Phase I — Restore the 4 ingest sources alongside the universal converter

**The problem.** When `NewDatasetV2.tsx` replaced `NewDataset.tsx`, the four source modes (file upload, URL, web scrape, S3) were collapsed to "file upload only". The URL / scrape / S3 backend endpoints still exist (`apps/api/routers/ingest.py` — `/upload/preview`, `/url/preview`, `/scrape/preview`, `/s3/preview`, `/finalize`) but nothing in the UI calls them anymore.

**Design.** Single page, tabbed source selector at the top: `[File] [URL] [Web scrape] [S3]`. Below the tabs, the shared bottom half is unchanged from `NewDatasetV2`: detected format card, sample records, predicted train/valid/canary counts, "auto-convert via Ollama" toggle. The auto-convert toggle applies regardless of source — pull from S3 → preview → if format is recognized, parse directly; if unknown, fall back to Ollama.

**Files to touch.**

- `apps/web/src/pages/NewDatasetV2.tsx` — add tab selector + URL/S3/scrape forms, route the preview through whichever endpoint matches the active tab.
- `apps/api/routers/ingest_v2.py` — add `POST /file-from-url` and `POST /file-from-s3` that internally fetch then delegate to the same `parse_known → ollama fallback → write_dataset` pipeline that `POST /file` already uses.
- Existing `ingest.py` endpoints stay for backward compat but are no longer the primary path.

**Effort.** ~3 hr. Small.

---

## Phase J — Chat context tab: full template library

**The problem.** The right-hand "Context" panel on `/chat` shows three example prompts. Users don't discover what the agent can do.

**Design.** A categorized library of clickable templates covering the full SLM-Forge lifecycle. Click any template → it inserts the prompt into the textarea and focuses it (user can edit before sending). Templates grouped by workflow phase.

| Category | Sample templates |
|---|---|
| **Datasets** | "list all datasets", "preview the first 5 rows of `<name>`", "synthesize 200 examples from `<name>` in the same style", "check `<name>` for quality issues" |
| **Experiments** | "start an autoresearch experiment on `<name>` for 6 rounds", "what was the best hyperparam combo last week?", "compare runs 12 and 15" |
| **Runs** | "list running runs", "show me metrics for run `<id>`", "why did run `<id>` fail?", "summarize the last 10 completed runs" |
| **Hermes / Autoresearch** | "propose the next mutation for session `<id>`", "what's the canary drift trend?", "should I stop session `<id>` or keep going?" |
| **Exports** | "export run `<id>` to GGUF with Q4 + Q5", "list completed exports", "estimate the iPhone size for run `<id>`" |
| **Diagnostics** | "is Ollama up?", "show the last 50 lines from the trainer log", "what's the disk usage under /runs?" |

**Files.**

- `apps/web/src/components/ChatTemplates.tsx` — new. Renders the categorized list.
- `apps/web/src/pages/Chat.tsx` — replace the right-side aside contents with `<ChatTemplates onPick={text => setInput(text)} />`. Wire `setInput` so clicking inserts the template.

**Effort.** ~2 hr. Small.

---

## Phase K — R&D tab: market research engine

**The problem.** No way to keep tabs on competitors. The user wants a "trigger research" workflow that produces saved markdown reports.

**Design.**

1. **Backend research engine** at `packages/research/engine.py`.
   - Inputs: `topic` (free text, e.g. "fine-tuning frameworks for Apple Silicon"), `depth` (`quick` / `standard` / `deep`).
   - Algorithm: build a multi-turn prompt to Ollama (qwen3:30b-a3b). Outline → fill sections → comparison table → recommendations. For `deep` depth, do 3 passes with self-critique.
   - Output: markdown string with frontmatter (`title`, `topic`, `depth`, `generated_at`, `model`, `tags`).
   - Save to `docs/market-research/<YYYYMMDD-HHMMSS>-<slug>.md`.
   - **Optional web grounding (defer to Phase K.2).** First version uses pure Ollama knowledge; later phase adds a `web_search` tool call before generation.

2. **API router** `apps/api/routers/research.py`.
   - `POST /api/v1/research/start` body `{topic, depth}` → returns `{job_id}` and starts an `asyncio.create_task`.
   - `GET /api/v1/research/jobs/{id}/stream` → SSE: `outline | section_done | written | error`.
   - `GET /api/v1/research/reports` → list every file in `docs/market-research/` with parsed frontmatter.
   - `GET /api/v1/research/reports/{filename}` → return raw markdown.
   - `DELETE /api/v1/research/reports/{filename}` → admin-only (Phase M; for now allow).

3. **Frontend** new `/research` route.
   - Nav entry: "R&D" in `Nav.tsx`.
   - Layout: left rail with report list (most recent first, tag chips), main pane renders selected report (re-using the markdown helper already in `DatasetDetail.tsx`).
   - Top-right "+ New report" button → modal: topic textarea, depth radio, generate. Live progress drawer.

**Files.** `packages/research/__init__.py`, `packages/research/engine.py`, `apps/api/routers/research.py`, `apps/web/src/pages/Research.tsx`, `apps/web/src/components/ResearchModal.tsx`, integration into `Nav.tsx`/`App.tsx`/`main.py`.

**Effort.** ~6 hr without web grounding. +4 hr for K.2 (web grounding).

---

## Phase L — Observability: structured logging + Loki + Prometheus + Grafana

**The problem.** Current logs are plain-text. Loki/Grafana filtering by `run_id`, `user_id`, `service` is impossible without structured fields.

**Design — three independent pieces.**

### L.1 — Structured JSON logging in every Python process

- Add `python-json-logger` to core deps.
- New `packages/_logging.py` upgrade: when env `SLM_FORGE_LOG_FORMAT=json` is set, install `JsonFormatter` on every handler instead of the plain-text formatter. Default stays plain-text for local dev.
- Every log line gets these fields (auto-injected):
  ```
  ts, level, service, logger, msg
  ```
  Plus, when present in a contextvar:
  ```
  request_id, user_id, run_id, session_id, trace_id
  ```
- New `apps/api/middleware/request_context.py`:
  - Generate `request_id = uuid4()` per request.
  - Extract `user_id` from JWT (Phase M) — until then, "anonymous".
  - Stash both in `contextvars.ContextVar`.
  - The custom `JsonFormatter` reads contextvars at format time.
- Workers (trainer / ratchet / exporter) write `run_id` and `session_id` into the contextvar before each run starts.

### L.2 — Prometheus `/metrics` endpoint

- Add `prometheus-client` to core deps.
- New `apps/api/routers/metrics.py` exposing `GET /metrics` in the Prometheus text format.
- Metrics:
  - `slmforge_http_requests_total{method, route, status}` (Counter)
  - `slmforge_http_request_duration_seconds{method, route}` (Histogram)
  - `slmforge_runs_total{status}` (Counter)
  - `slmforge_active_runs` (Gauge)
  - `slmforge_worker_heartbeat_age_seconds{worker}` (Gauge, scraped from DB)
  - `slmforge_dataset_count` (Gauge)
  - `slmforge_canary_drift{session_id}` (Gauge)
  - `slmforge_chat_messages_total{role}` (Counter)
- Middleware records request metrics automatically.

### L.3 — Observability docker-compose overlay

- New `docker-compose.observability.yml` (start with `docker compose -f docker-compose.yml -f docker-compose.observability.yml up`):
  - `loki` (port 3100)
  - `promtail` — scrapes `/app/runs/_*.log`, `/app/runs/*/training.log`, and JSON logs from stdout via docker driver; adds labels `service`, `env=local`
  - `prometheus` (port 9090) — scrapes `slm-forge-api:8000/metrics` every 15s
  - `grafana` (port 3001, admin/admin default) — pre-provisioned datasources for Loki + Prometheus, plus three starter dashboards:
    - **SLM-Forge Overview** — request rate, p95 latency, worker heartbeats, active runs
    - **Runs Detail** — train_loss / val_loss / canary_loss time series by run_id
    - **Logs Explorer** — Loki LogQL with starter queries (`{service="api"}`, `{run_id="42"}`)

**Files.** ~12 new + edits to `packages/_logging.py`, `pyproject.toml`, `apps/api/main.py`. Dashboards as JSON in `observability/grafana/dashboards/`.

**Effort.** ~12 hr. Largest single phase. Can ship in three independent sub-phases (L.1 → L.2 → L.3).

---

## Phase M — Keycloak + OPA: production-grade AAA

**Your instinct is correct: use both, separated cleanly.**

- **Keycloak** owns **identity**. Login UI, user/group management, OIDC token issuance, federation with corporate IdP later. It is the "who you are" service.
- **OPA** owns **policy decisions**. Given identity claims, what can this user actually do? Policies-as-code in Rego, hot-reloadable, testable. It is the "what you can do" service.

You could get by with Keycloak alone (it has RBAC), but coarse role checks (`is admin?`) don't scale to questions like "can this data engineer access *this specific dataset*?" That's where OPA shines — fine-grained, declarative, audit-friendly.

### Architecture

```
Browser ──login──► Keycloak (port 8080)
   │
   │ (gets access_token JWT)
   │
   ▼
SLM-Forge UI ──Bearer token──► FastAPI middleware
                                   │
                                   ├── 1. Verify JWT signature against Keycloak JWKS
                                   ├── 2. Extract user_id, roles, groups
                                   ├── 3. Attach to request.state.user + contextvar
                                   ├── 4. Build OPA input: {user, roles, action, resource, ...}
                                   ├── 5. POST opa:8181/v1/data/slm_forge/allow
                                   └── 6. If allow=false → 403 with reason
```

### M.1 — Keycloak setup

- New service in `docker-compose.yml`: `keycloak:25` with realm import.
- New `keycloak/realm-export.json` defining:
  - Realm `slm-forge`
  - Client `slm-forge-web` (public, redirect URIs for localhost:5173)
  - Client `slm-forge-api` (bearer-only, for token validation)
  - Six roles (see Phase M.3)
  - Two seed users for testing (`admin@local`, `engineer@local`) with passwords from `.env`.
- Frontend: add `oidc-client-ts` (small lib). New `apps/web/src/auth/keycloak.ts` handles login/logout/silent refresh.
- Protected route wrapper around every existing route in `App.tsx`.

### M.2 — OPA setup + Rego policies

- New service in compose: `openpolicyagent/opa:latest-rootless` on port 8181, mounting `policies/` as bundle source.
- `policies/slm_forge.rego` — main entrypoint:
  ```rego
  package slm_forge

  default allow = false

  allow {
      input.user.roles[_] == "admin"
  }

  allow {
      role_permits[role][input.action][_] == input.resource
      input.user.roles[_] == role
  }
  ```
- `policies/role_matrix.rego` — declarative role → action → resource map (see Phase M.3).
- `policies/slm_forge_test.rego` — unit tests run via `opa test policies/`.

### M.3 — Roles & permissions matrix

| Role | datasets | experiments | runs | exports | logs | settings | research | chat |
|---|---|---|---|---|---|---|---|---|
| **admin** | CRUD | CRUD | CRUD | CRUD | RW | RW | CRUD | RW |
| **data_engineer** | CRUD | CRUD | R + cancel | execute | R | — | R | RW |
| **domain_expert** | R + update README | R | R | R | — | — | RW | RW |
| **devops** | — | — | R | — | RW | RW | R | R |
| **operations** | R | R | R | execute | R | — | R | R |
| **support** | R | R | R | R | R | — | R | R |

Each cell maps to one or more `(action, resource)` rules in Rego.

### M.4 — FastAPI middleware

- New `apps/api/middleware/auth.py`:
  - `verify_jwt(token) → User` (validates against cached Keycloak JWKS).
  - `policy_check(user, action, resource) → bool` (POSTs to OPA, 200ms timeout, fail-closed).
  - Route decorator `@requires(action, resource_kind)` — wraps endpoints, returns 401/403 with clear reason.
- New `apps/api/services/auth_settings.py`:
  - `SLM_FORGE_AUTH_ENABLED` (default `false` for backward compat) — when false, every request gets a synthetic `admin` user and OPA is bypassed.
  - This makes the entire stack opt-in: existing local-dev workflow is unchanged.

### M.5 — Frontend integration

- Login button in `Nav.tsx` (right side) — shows user email + role pill when logged in, "Sign in" otherwise.
- New `/admin/users` page (admin role only) — lists Keycloak users + assigned roles, link out to Keycloak Admin Console for full management.
- 403 responses surface as a toast: "Your role (data_engineer) cannot delete this export. Ask an admin."

**Files.** ~18 new + edits to compose, every router. Phase M.4 + M.5 can be deferred behind feature flag.

**Effort.** ~16 hr. Largest phase. Recommend splitting into M.1 (Keycloak only, sign-in works, no enforcement), M.2+M.3 (OPA running but enforcement off), M.4 (turn on enforcement), M.5 (admin UI).

---

## Phase N — Hermes skill expansion + agent leverage

This is where SLM-Forge gets ahead of LlamaFactory / Axolotl / Unsloth. Those competitors give you knobs; SLM-Forge can give you a **reasoning loop**.

### N.1 — Audit of current skills (5 exist, only 1 is actively called)

| Skill | Used? | Where |
|---|---|---|
| `propose_hyperparam_mutation` | ✓ Active | `packages/ratchet/loop.py` |
| `analyze_canary_drift` | ✗ Dormant | Should fire when drift > threshold |
| `diagnose_mps_oom` | ✗ Dormant | Should fire when a run fails with OOM in `training.log` |
| `ingest_dataset` | ✗ Dormant | Should plug into Phase I auto-convert as the "smart converter" |
| `select_method_for_task` | ✗ Dormant | Should be a button on New Experiment form |

First sub-phase: wire the four dormant skills into the right places. Zero new skill files, just integrate.

### N.2 — New skills to add (~8 new markdown files in `.hermes-skills/`)

| Skill | Trigger / Use |
|---|---|
| `data_quality_review` | "Review" button on DatasetDetail. Returns: duplicate rows, length outliers, missing prompt-templates, suggested fixes. |
| `propose_canary_set` | When a dataset has no canary, suggest 5 edge-case records. Surfaced on DatasetDetail. |
| `synthesize_style_prompt` | Generate the dataset-specific synthesis prompt that Phase G's engine then uses. Improves output quality vs the current generic prompt. |
| `explain_metric_anomaly` | Auto-fires when val_loss > 1.5 × train_loss. Surfaces as a chip on RunDetail. |
| `recommend_export_quants` | "Suggest" button on Exports page. Recommends quant levels based on model size + target device. |
| `model_selection` | "Pick base model" button on New Experiment. Given dataset + task description, recommends Qwen vs Llama vs Gemma. |
| `failure_post_mortem` | "Ask Hermes" button on any failed run. Generates a markdown post-mortem with cause + 1-line fix. |
| `auto_label_unlabeled` | For raw-text datasets, generate prompt+completion pairs without explicit Q&A structure. |

### N.3 — Multi-step Agents (chains of skills)

Single-shot skill calls aren't enough for real research workflows. Wrap chains as LangGraph subgraphs callable from the chat UI:

- `experiment_recommender` — `data_quality_review → model_selection → propose_hyperparam_mutation → expected_outcome`. One chain produces a complete "here's how to fine-tune this dataset" plan.
- `evaluation_designer` — given a dataset, propose canary set + benchmark questions + success criteria.
- `optimization_coach` — analyze last N runs of a session and recommend continue / pivot / stop.
- `incident_responder` — when a worker heartbeat goes stale, auto-run `failure_post_mortem` on the last run and post to the dashboard as a "needs attention" notice.

### N.4 — UI touchpoints

- "Ask Hermes" button on every error message (uses `failure_post_mortem`).
- "Suggest" button on New Experiment (uses `select_method_for_task` + `model_selection`).
- "Review" button on Dataset detail (uses `data_quality_review`).
- "Auto-explain" chip on RunDetail when anomalies detected (uses `explain_metric_anomaly`).
- Chat templates (Phase J) include "ask the optimization coach", "run a market scan", etc.

**Effort.** ~10 hr for N.1 + N.2. ~6 hr for N.3 (LangGraph subgraphs). ~4 hr for N.4. Total ~20 hr; ships in slices.

---

## Cross-cutting dependency graph

```
                       Phase I   ──┐
                       Phase J     │
                                   ├──► merge round 1 (low risk)
                       Phase N.1   │
                       Phase N.2   │
                                ──┘

  Phase L.1 ──► Phase L.2 ──► Phase L.3      (sequential, independent track)

  Phase M.1 ──► Phase M.2 ──► Phase M.3 ──► Phase M.4 ──► Phase M.5

  Phase K (standalone, can interleave anywhere)
```

Phases I, J, K, N have **no dependency** on L or M and can ship first. Phase L and M are the bigger investments and should be planned per their own dependency chains.

---

## Recommended rollout order

1. **Phase I + J + N.1** — lowest risk, biggest immediate UX win. ~8 hr total.
2. **Phase K** — R&D tab with Ollama-only research first; web grounding later. ~6 hr.
3. **Phase N.2 + N.4** — new skills + UI buttons. ~14 hr.
4. **Phase L.1** — structured JSON logging (foundation for everything observability + AAA logs). ~3 hr.
5. **Phase L.2 + L.3** — Prometheus + Loki/Grafana stack. ~9 hr.
6. **Phase M.1 + M.2 + M.3** — Keycloak + OPA running, policies defined, enforcement OFF. ~10 hr.
7. **Phase M.4 + M.5** — turn enforcement on, ship admin UI. ~6 hr.
8. **Phase N.3** — multi-step agents. ~6 hr.
9. **Phase K.2** — web grounding for market research. ~4 hr.

Grand total ~66 hr engineering. Recommend slicing into one-phase merges so each is independently shippable.

---

## Open questions for Amit

1. **Web grounding for R&D (Phase K.2):** is there a web search API/MCP you want me to use? Or pure Ollama for now and search later?
2. **Keycloak deployment:** are you OK with running Keycloak as a Docker service alongside the API, or do you have a corporate Keycloak instance to integrate with?
3. **Grafana login:** OK to ship with default `admin/admin` for local dev, with a `.env` override for any non-trivial deploy?
4. **Roles in M.3:** the matrix above is my proposal. Want to change any cell before I start writing Rego?
5. **Failure post-mortems (N.2):** should they be written to disk as part of the run artifacts (under `runs/<id>/post_mortem.md`), or shown in-UI only?

Reply with which phase to start with and I'll get into implementation.
