# apps/api — FastAPI backend

Runs in Docker (`:8000`). FastAPI + SQLModel + sse-starlette over SQLite.

## Layout
- `main.py` — app construction, lifespan, `_recover_stranded`, `PlatformInfo`, `PrometheusMiddleware`.
- `models/` — SQLModel tables. Key: `Run` (run.py), `TrainingSession` (session.py), `Metric`, `Export`, `HermesTrace`, `ChatConversation`, `ChatMessage`, `WorkerHeartbeat`, `AutoFixAttempt`.
- `routers/` — one module per resource (`runs.py`, `sessions.py`, `agents.py`, `hermes.py`, `chat.py`, `synth.py`, `ingest.py`, `ingest_v2.py`, `research.py`, `traces.py`, `logs.py`, `exports.py`, `datasets_detail.py`, `admin.py`, `autofix.py`).
- `services/` — `db.py` (engine + migrations `init_db()`), `model_catalog.py` (`validate_run_request`), `qa_store.py`, `tenant.py` (`current_tenant`), `auth_settings.py`, `remedy.py` (`translate_error`).
- `middleware/` — `auth.py` (JWT/Keycloak + service-token bypass, `User`, `requires(...)` dep), `metrics.py` (`PrometheusMiddleware`).

## Important invariants
- DB schema: `SQLModel.create_all` builds initial; additive migrations live in `services/db.py` (`_RUN_MIGRATIONS`, `_SESSION_MIGRATIONS`). To add a column, register here with a default — never hand-edit prod schema.
- Run claim queue: `POST /api/v1/runs/claim` filtered by `trainer_backend` (atomic compare-and-swap + lease recovery). Workers don't poll-and-pick.
- Catalog enforcement: `validate_run_request(base_model, trainer_backend)` called in `runs.create_run` *and* `sessions.SessionCreate` path → 422 on bad combo. Bypass: `SLM_FORGE_ENFORCE_CATALOG=false`.
- Tenant boundary threaded through queries via `services/tenant.py:current_tenant()` — required in every router.
- Auth dep: `requires("scope")` from `middleware/auth.py:379`. Off by default unless `SLM_FORGE_AUTH_ENABLED=true`.
- Models endpoint for FE: `/api/v1/models/v2` filtered by selected backend.
