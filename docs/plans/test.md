SLM-Forge: Trace nesting, agent runs, multi-tenancy, and Apache Ozone                                         
                                                                                                               
 Context                                                                                                     
                                                                                                             
 Five issues coalesce into one architectural initiative. Today:

 1. Traces tab shows only skill spans. hermes_traces is a flat table with no parent/trace linkage; span "kind"
  is inferred from a free-form source string. Agent runs leave no top-level row.
 2. Agents tab's "Run agent" produces no observable activity. The endpoint POST /api/v1/agents/{name}/run
 exists (apps/api/routers/agents.py:220-239) and streams SSE, but the agent run is not traced as a parent span
  — so even when it works, the user can't see it on the Traces tab. The button may also be silently failing on
  the SSE path.
 3. No tenant isolation. Run, Session, Export, Metric, AutoFix have no tenant_id/user_id columns. List
 endpoints (sessions.py:70, runs.py, exports, metrics) return global state. hermes_traces and chat_* already
 have these columns, but they're set to 'default'.
 4. Artifacts live on the host filesystem under hardcoded paths (/app/{data/datasets,runs,exports}); upload
 handlers buffer entire archives into memory (runs.py:253). 8 call sites read/write disk directly.
 5. Traces filter chip is misleading — activeFilterCount (Traces.tsx:226-234) computes (skillFilter.size > 0 ?
  1 : 0), so selecting 5 skills still reads "1 filter".

 Intended outcome: a tenant-and-user-isolated lab where every Run, Session, Export, Trace, and Artifact is
 scoped to a (tenant, role, user) identity established by Keycloak; agent runs appear as nested traces with
 their child skill spans; all artifacts persist in Apache Ozone deployed via Helm in a kind cluster; auth is
 mandatory; existing on-disk artifacts stay readable for 30 days through a read-only fallback.

 Decisions locked from clarification

 - Local k8s: kind — extraPortMappings for the S3 gateway; local-path storage class.
 - Role semantics: permission tier — reuse policies/role_matrix.rego. The Role segment of the artifact key
 path = the user's highest realm role at write time.
 - Disk migration: fresh start — Ozone starts empty. Old disk artifacts stay readable via
 SLM_FORGE_DISK_FALLBACK=true until 2026-07-29, then removed.
 - Auth posture: always mandatory — no anonymous mode. Workers identify as a Keycloak service account via
 client_credentials grant.

 Scope split — spec + plan files to create on approval

 This planning doc decomposes into the project's standard layout (docs/specs/, docs/plans/):

 - docs/specs/2026-06-29-traces-and-agents-fixes.md + docs/plans/2026-06-29-traces-and-agents-fixes.md — Phase
  A.
 - docs/specs/2026-06-29-agent-trace-nesting.md + docs/plans/2026-06-29-agent-trace-nesting.md — Phase B.
 - docs/specs/2026-06-29-multi-tenancy-identity.md + docs/plans/2026-06-29-multi-tenancy-identity.md — Phase
 C.
 - docs/specs/2026-06-29-apache-ozone-storage.md + docs/plans/2026-06-29-apache-ozone-storage.md — Phase D.

 Each spec: scope / I/O / data models / interfaces / constraints / non-goals. Each plan: dated phases, ≥3
 red-team passes, acceptance criteria. CLAUDE.md DoD applies per phase.

 Phase A — Bug fixes (1 day)

 A1 — Traces filter chip semantics

 - File: apps/web/src/pages/Traces.tsx:226-234.
 - Bug: (skillFilter.size > 0 ? 1 : 0) treats N selected skills as 1 filter.
 - Fix: + skillFilter.size (so 3 selected skills + a status filter = 4). Add label "N filter(s) active" next
 to the chip. Right pane stays rows.length but relabel as "N matching trace(s)".

 A2 — Traces left-panel "Skill Activity" click does nothing

 - Same file. Confirm the click handler on each row in the left "Skill Activity" panel toggles skillFilter (a
 Set<string>) — if it doesn't, wire it. Re-fetch traces on toggle (already happens via useEffect).

 A3 — Agents "Run agent" silent no-op

 - Files: apps/web/src/pages/Agents.tsx:145-186 and apps/api/routers/agents.py:220-239.
 - Diagnose before fixing: run the dev stack and watch the network panel. Likely root cause is one of:
   - SSE reader is fetch + ReadableStream (correct — EventSource can't carry an Authorization header). Verify
 authFetch is in fact attaching the Bearer.
   - _prepare_args() rejects the loose per-agent payload schema silently.
   - stream_agent (in packages/agents/runner.py) raises and the SSE error event isn't surfaced in the UI.
 - Fix: surface error events into a toast + console; ensure Authorization is attached; tighten the
 input-validation error shape.
 - After Phase B, the agent run also produces a top-level trace — that becomes the secondary confirmation that
  the button worked.

 Phase B — Agent traces with nested skill spans (2 days)

 B1 — Schema migration (additive, reversible)

 - File: apps/api/services/db.py — add _TRACE_MIGRATIONS (mirror the (col_name, "TYPE DEFAULT X") shape of
 _RUN_MIGRATIONS:24-46).
 - New columns on hermes_traces: kind TEXT DEFAULT 'skill', trace_id TEXT, parent_span_id TEXT, agent_run_id
 TEXT. Indexed on trace_id and agent_run_id.
 - File: apps/api/models/hermes_trace.py:18-62 — add matching SQLModel fields.

 B2 — Tracing context manager

 - New file: apps/api/services/tracing.py.
 - Public API:
 async with trace_span(kind: Literal["agent","skill","tool"], name: str, **attrs) as span:
     ...
 - Writes a row to hermes_traces with auto-populated trace_id (UUIDv7 if root, inherited from a
 contextvars.ContextVar otherwise) and parent_span_id (popped from the stack).
 - Extract the existing trace-write helper from packages/agents/runner.py and packages/research/... into this
 module so all callers share one writer.

 B3 — Wrap agent runs

 - File: packages/agents/runner.py — each
 run_experiment_recommender/run_optimization_coach/run_evaluation_designer/run_incident_responder opens
 trace_span(kind="agent", name=agent_id, agent_run_id=uuid7()). LangGraph nodes that call Hermes skills
 inherit trace_id automatically through the contextvar — no per-node changes needed.

 B4 — Frontend nested rendering

 - File: apps/web/src/pages/Traces.tsx.
 - API change: GET /api/v1/hermes/traces accepts group_by=trace (returns tree rows grouped by trace_id with
 children inline) and kind=agent|skill|tool filter. Backwards-compatible default unchanged.
 - UI: when group_by=trace, agent spans render as expandable parent rows with child skill spans nested.
 Flat-table view remains a tab.

 Phase C — Multi-tenancy + identity (3 days)

 C1 — Identity dependency

 - New file: apps/api/deps.py.
 - def current_identity(request: Request) -> Identity reads request.state.user (already populated by
 apps/api/middleware/auth.py:252) and returns Identity(tenant_id, role, user_id, email, scopes).
 - tenant_id ← first group from user.groups stripped of leading / (Keycloak /tenants/acme → acme). Hard-error
 if missing.
 - role ← highest-privilege role from user.roles against the order in role_matrix.rego (admin > devops >
 data_engineer > domain_expert > operations > support > viewer).

 C2 — Database tenant columns

 - File: apps/api/services/db.py.
 - Extend _RUN_MIGRATIONS and _SESSION_MIGRATIONS with tenant_id TEXT, user_id TEXT, role TEXT. Add new
 _EXPORT_MIGRATIONS, _METRIC_MIGRATIONS, _AUTOFIX_MIGRATIONS lists wired into init_db().
 - Files: apps/api/models/{run,session,export,metric,autofix}.py — add the matching SQLModel fields.
 - All defaults NULL on existing rows; new rows MUST populate (enforced in handlers + a NOT-NULL constraint
 added in a follow-up contract migration after backfill).

 C3 — Scoping helper

 - New file: apps/api/services/scoping.py.
 - def scope(query, identity, model) returns query.where(model.tenant_id == identity.tenant_id, model.user_id
 == identity.user_id) for non-admins; for admins, scopes by tenant_id only.
 - One-line change at every list/get/update/delete handler. Find sites with:
 rg "select\((Run|TrainingSession|Export|Metric|AutoFix)\)" apps/api/routers/.

 C4 — Worker service identity

 - New file: packages/common/auth.py.
 - On worker boot: call Keycloak /realms/{realm}/protocol/openid-connect/token with
 grant_type=client_credentials&client_id=slm-forge-worker&client_secret=$SLM_FORGE_WORKER_SECRET. Cache JWT
 until exp - 60s; refresh on demand.
 - Attach Authorization: Bearer <jwt> to every API call from packages/{trainer,ratchet,exporter}.
 - Worker JWT carries realm_role=worker, groups=[/tenants/system]. OPA grants the worker role: claim_run,
 update_run, upload_artifact — only when the run/export is already claimed-by-this-worker. Workers cannot
 enumerate other tenants' resources.

 C5 — OPA policy extensions

 - Files: policies/role_matrix.rego, new policies/tenant_isolation.rego, update policies/slm_forge.rego.
 - New rules:
 same_tenant := input.user.tenant_id == input.resource.tenant_id
 same_owner  := input.user.user_id   == input.resource.user_id
 - allow requires same_tenant for every read/write; same_owner required for reads except where role grants
 cross-user (admin only).
 - Extend policies/slm_forge_test.rego with: same-tenant same-user (allow), same-tenant other-user non-admin
 (deny), other-tenant any-user (deny), worker writing to any tenant's claimed run (allow), worker reading
 another tenant's runs (deny).

 C6 — Frontend identity context

 - File: apps/web/src/auth/AuthContext.tsx.
 - Extend AppUser with tenant_id: string, role: string. Populate from JWT claims (groups[0], primary role).
 - Add a tenant pill in the top nav. Find via rg "navigation\|nav-link\|TopBar\|Header" apps/web/src/.

 C7 — Remove auth-disable mode

 - File: apps/api/middleware/auth.py.
 - Drop the enforce=False short-circuit. make auth ENABLED=false prints a deprecation warning and refuses to
 start.
 - Makefile — update help text. auth-token target stays for minting dev JWTs.
 - Keycloak realm seed: extend the realm-export JSON (locate via rg -l "realm" deploy/ docker-compose* during
 implementation) with 2 demo tenants × 3 roles × 2 users + a slm-forge-worker confidential client.

 Phase D — Apache Ozone storage (4 days)

 D1 — Deployment (kind cluster)

 - New directory: deploy/ozone/.
 - deploy/ozone/values.yaml — Helm override for the official ozone-helm-charts chart
 (https://ozone.apache.org/docs/quick-start/installation/kubernetes). Sized for kind: 1 OM, 1 SCM, 3
 DataNodes, 1 S3 Gateway. PVCs use local-path (default storage class on kind via Rancher
 local-path-provisioner).
 - deploy/ozone/kind-config.yaml — extraPortMappings for s3g (9878) and om (9874) so the API container reaches
  them at host.docker.internal:9878.
 - Makefile targets:
   - ozone-up — kind create cluster --config deploy/ozone/kind-config.yaml; helm repo add apache-ozone
 https://apache.github.io/ozone-helm-charts && helm install ozone apache-ozone/ozone -f
 deploy/ozone/values.yaml -n slm-forge-ozone --create-namespace.
   - ozone-down — helm uninstall ozone -n slm-forge-ozone && kubectl delete ns slm-forge-ozone.
   - ozone-bootstrap — invokes scripts/ozone_bootstrap.py that creates the slm-forge volume + per-tenant
 buckets via the S3 API.
   - ozone-status — kubectl get pods -n slm-forge-ozone + aws --endpoint-url=http://localhost:9878 s3 ls.

 D2 — Storage abstraction

 - New package: apps/api/services/storage/.
   - base.py — ABC ObjectStore with async methods: put(key, fileobj) -> ObjectMeta, get(key) ->
 AsyncIterator[bytes], delete(key), head(key) -> ObjectMeta | None, list(prefix, limit) -> list[ObjectMeta],
 presign_get(key, ttl), presign_put(key, ttl).
   - ozone.py — implementation via aioboto3 S3 client; endpoint SLM_FORGE_OZONE_S3_ENDPOINT; creds from
 env/secret.
   - local.py — filesystem implementation for tests + the 30-day disk fallback. Reads existing data/, runs/,
 exports/ layout.
   - factory.py — get_object_store(identity) returns an Ozone-backed store scoped to the right bucket.
   - tenancy.py — on user login (auth.py post-validation), idempotently ensures slm-forge-{tenant_id} bucket
 exists (HeadBucket + CreateBucket).
 - Key scheme: bucket slm-forge-{tenant_id}, key
 {role}/{user_id}/{exports|runs|data}/{artifact_id}/{filename}.

 D3 — Replace storage call sites

 Public HTTP contract stays identical so workers don't change beyond the streaming refactor below.

 - apps/api/routers/runs.py:237-275 — multipart adapter upload: stream-extract tarball into Ozone keys
 {...}/runs/{run_id}/adapter/{member.name} instead of tarfile.extractall. Replace archive.file.read() with
 chunked .read(64*1024) loop.
 - apps/api/routers/datasets.py:66-86 — dataset archive download: StreamingResponse(storage.get(...)) instead
 of disk-read into BytesIO.
 - apps/api/routers/datasets_detail.py:172-212 — JSONL row reads via async line iterator on storage.get(...).
 - packages/exporter/pipeline.py:191,198-202,287,315 — read adapter via storage; write fused + GGUF outputs
 through the API (exporter runs on host, no direct Ozone access).
 - apps/api/routers/exports.py:185-242 — StreamingResponse(storage.get(...)). Drop the 3-fallback
 _to_container_path strategy.
 - apps/api/services/post_mortem.py:79,107 — log reads via storage.
 - packages/trainer/transfer.py:62-63 — dataset cache to per-worker scratch dir (unchanged; that's worker
 cache, not source-of-truth).

 D4 — Streaming uploads

 - apps/api/routers/runs.py:237 currently calls archive.file.read() (loads entire tarball into memory).
 - Replace with await storage.put(key, AsyncChunkReader(archive.file, chunk_size=64*1024)). RSS must stay flat
  during a 1 GB upload.

 D5 — 30-day disk fallback

 - File: apps/api/services/storage/factory.py.
 - If SLM_FORGE_DISK_FALLBACK=true AND today < SLM_FORGE_DISK_FALLBACK_UNTIL=2026-07-29, wrap the Ozone store
 with a fallback decorator: on head/get 404, try the legacy disk path; log a warning each time. Past the date
 the flag is ignored (hard-coded check).

 Phase E — Testing (TDD throughout)

 Tests precede implementation per phase. Coverage target ≥90% (project DoD).

 - tests/web/test_traces_filter_count.spec.tsx — Phase A: selecting 3 skills → chip reads "3 filter(s)
 active".
 - tests/api/test_agents_run.py — Phase A+B: POST /api/v1/agents/experiment_recommender/run; assert SSE emits
 stage + complete; assert a hermes_traces row appears with kind='agent' and ≥1 child kind='skill' rows sharing
  the same trace_id.
 - tests/api/test_trace_nesting.py — Phase B: parent/child contextvar correctness.
 - tests/api/test_traces_router.py — Phase B: GET /traces?group_by=trace shape.
 - tests/web/test_traces_tree.spec.tsx — Phase B: expand/collapse.
 - tests/api/test_tenancy_isolation.py — Phase C: 2 tenants × 2 users; tenant B cannot list or fetch tenant
 A's runs (403); admin in tenant A sees both users.
 - tests/api/test_worker_identity.py — Phase C: worker token claims & uploads; worker cannot enumerate other
 tenants.
 - policies/slm_forge_test.rego extensions — Phase C: full deny/allow matrix.
 - tests/api/test_storage_local.py — Phase D: LocalObjectStore against tmp_path.
 - tests/api/test_storage_ozone.py — Phase D: round-trips put/get/list/delete against kind-cluster Ozone
 (skipped unless SLM_FORGE_OZONE_TESTS=true).
 - tests/api/test_disk_fallback.py — Phase D: disk read when Ozone returns 404 + flag on.
 - tests/api/test_streaming_upload.py — Phase D: 500 MB synthetic tar.gz; assert RSS < 200 MB during upload.
 - tests/e2e/test_two_tenant_e2e.py — full stack: train→export→download on two tenants in parallel, assert no
 cross-leak in DB or Ozone.

 Phased delivery (4 PRs)

 Per CLAUDE.md rule "No versioned code modules" + "spec-driven", each phase ships as a single feature branch +
  PR with its spec/plan committed alongside.

 1. PR-1 (Phase A): fix/traces-filter-and-agents-run — bug triage. ~6 files touched.
 2. PR-2 (Phase B): feat/agent-trace-nesting — schema migration + tracing.py + UI tree.
 3. PR-3 (Phase C): feat/multi-tenancy-identity — most invasive. Migrations + scoping helper + OPA + worker
 identity + remove disable-auth. Coordinate with anyone using the lab — they'll need to log in once.
 4. PR-4 (Phase D): feat/apache-ozone-storage — deploy/, storage/ package, replace 8 call sites, streaming
 upload. Gate behind SLM_FORGE_STORAGE=ozone for one release while the default stays local; flip after the
 end-to-end smoke passes.

 Critical files to modify

 Backend:
 - apps/api/services/db.py (additive migrations for 5 models + traces)
 - apps/api/middleware/auth.py:252 (remove disable mode)
 - apps/api/deps.py (new — identity dep)
 - apps/api/services/identity.py (new)
 - apps/api/services/scoping.py (new)
 - apps/api/services/tracing.py (new)
 - apps/api/services/storage/{base,ozone,local,factory,tenancy}.py (new)
 - apps/api/models/{run,session,export,metric,autofix,hermes_trace}.py
 - apps/api/routers/{runs,sessions,exports,datasets,datasets_detail,traces,agents,metrics,autofix}.py
 - packages/agents/runner.py (wrap with trace_span)
 - packages/common/auth.py (new — worker client_credentials)
 - packages/{trainer,ratchet,exporter}/* (Bearer token; API-routed artifact IO)
 - packages/exporter/pipeline.py (storage abstraction)

 Frontend:
 - apps/web/src/pages/Traces.tsx (filter count + tree view)
 - apps/web/src/pages/Agents.tsx (SSE error surfacing)
 - apps/web/src/auth/AuthContext.tsx (tenant_id, role)
 - Top-bar nav component (tenant pill — find at implementation time)

 Policies:
 - policies/role_matrix.rego
 - policies/tenant_isolation.rego (new)
 - policies/slm_forge.rego
 - policies/slm_forge_test.rego (extend)

 Infra:
 - deploy/ozone/{values.yaml,kind-config.yaml} (new)
 - deploy/keycloak/realm-export.json (locate + extend)
 - Makefile (new targets: ozone-up/down/bootstrap/status/migrate)
 - .env.example (SLM_FORGE_OZONE_*, SLM_FORGE_WORKER_*, SLM_FORGE_DISK_FALLBACK_*)

 Reused functions / patterns

 - Migration pattern: _RUN_MIGRATIONS list in apps/api/services/db.py:24-46 — append, don't fork.
 - SSE pattern: EventSourceResponse in apps/api/routers/agents.py:220-239 and exports.py:125-162 — reuse for
 any new streaming endpoint.
 - JWT extraction: verify_jwt() in apps/api/middleware/auth.py:112 already surfaces id, email, roles, groups.
 - AuthFetch: apps/web/src/lib/api.ts:17 already attaches Bearer token — extend to surface 401/403 better in
 the Agents tab.
 - Run claim queue: POST /api/v1/runs/claim already authenticates worker-by-name; extend with JWT validation.
 - Existing tenant columns: apps/api/models/{hermes_trace,chat}.py already carry tenant_id, user_id — reuse
 the same column types/defaults.

 Verification (end-to-end, after all 4 PRs)

 1. make ozone-up && make ozone-bootstrap → kubectl get pods -n slm-forge-ozone all Running; aws
 --endpoint-url=http://localhost:9878 s3 ls lists tenant buckets.
 2. make dev → log in as alice@tenant-a in the UI, create a session + run.
 3. Log out, log in as bob@tenant-b → GET /api/v1/runs returns [].
 4. As bob, GET /api/v1/runs/<alice_run_id> → 403 (OPA-enforced).
 5. Click "Run agent" → SSE events stream in the UI; on Traces tab a top-level row with kind=agent expands to
 show 2–4 child skill spans.
 6. Apply 3 filters in Traces → chip reads "3 filter(s) active", right pane reads "N matching trace(s)".
 7. Train on a dataset → adapter upload streams (peek RSS during transfer; should be flat); artifact appears
 in Ozone at slm-forge-tenant-a/{role}/{user}/runs/{run_id}/adapter/.
 8. Delete an artifact via the API → Ozone object gone; disk-fallback path also clean.
 9. make opa-test → tenant matrix tests green.
 10. uv run pytest -q → full suite green; coverage ≥90% across changed modules.

 Open risks

 - Ozone S3 gateway + aioboto3: Ozone advertises S3 compatibility but presign edge cases may differ.
 Mitigation: D2 verifies presign in a day-one smoke test; if presign fails, stream through the API.
 - kind networking on macOS: host.docker.internal works from the cluster but adds latency. Fallback: kubectl
 port-forward svc/ozone-s3g 9878:9878.
 - Realm seed location not pinned during exploration. Phase C step C7 must locate or create the realm-export
 file.
 - Worker cross-tenant access: a worker may legitimately claim runs from any tenant. If a worker is
 compromised, it can read any claimed-to-it artifact. Mitigation: workers process one claim at a time, never
 enumerate other tenants, and every cross-tenant access is audit-logged via Phase B's trace stream.