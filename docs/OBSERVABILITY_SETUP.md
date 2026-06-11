# Observability — Setup & Usage Guide

A crisp, step-by-step guide for the SLM-Forge observability stack: **Prometheus** for metrics, **Loki + Promtail** for logs, **Grafana** as the UI. Everything is already wired in `docker-compose.observability.yml`; this doc explains what each piece does and how to use them day-to-day.

---

## 1. Architecture at a glance

```
SLM-Forge API ──/metrics──► Prometheus (9090)  ──► Grafana (3001)
       │                                              ▲
       └─structured logs──► runs/_*.log[.json] ──┐    │
                                                 ▼    │
                                              Promtail ──► Loki (3100) ──┘
```

- **Prometheus** scrapes `slm-forge-api:8000/metrics` every 15s.
- **Promtail** runs as a sidecar; it tails `./runs/_*.log` (text) and `./runs/_*.log.json` (JSON) and ships lines to Loki.
- **Loki** stores the log lines indexed by labels (`service`, `level`, `run_id`, `session_id`).
- **Grafana** is the single pane: dashboards for metrics, Explore for ad-hoc LogQL/PromQL.

---

## 2. Start the stack

```bash
# Core stack (API + UI + workers' dependencies):
docker compose up -d

# Observability overlay (run side-by-side):
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d

# Optional: enable JSON-format logs so Promtail can parse structured fields
echo "SLM_FORGE_LOG_FORMAT=json" >> .env
make trainer     # T1 — restart workers so they pick up the env
make ratchet     # T2
make exporter    # T3
```

Then open:
- **Grafana** → http://localhost:3001 (login `admin / admin`, you'll be prompted to change)
- **Prometheus** → http://localhost:9090 (no auth)
- **Loki** → http://localhost:3100 (raw API; you'll mostly use it via Grafana)

Stop the overlay: `docker compose -f docker-compose.yml -f docker-compose.observability.yml down`. The core stack keeps running.

---

## 3. Grafana — first-time setup

Datasources and dashboards are auto-provisioned. After login:

1. Top-left ☰ menu → **Connections → Data sources**. You should see two entries pre-configured:
   - `Prometheus` → `http://prometheus:9090` (default)
   - `Loki` → `http://loki:3100`
   Click each → **Test** to confirm green.

2. ☰ menu → **Dashboards**. Three are pre-imported:
   - **SLM-Forge Overview** — request rate, p95 latency, worker heartbeat ages, active runs.
   - **SLM-Forge Runs Detail** — `train_loss` / `val_loss` / `canary_loss` per run.
   - **SLM-Forge Logs Explorer** — three Loki LogQL panels pre-saved as starter queries.

3. If you ever want to re-import them, the JSON sources live at `observability/grafana/dashboards/*.json` and are mounted read-only into the Grafana container.

---

## 4. Prometheus — metric reference

Open http://localhost:9090, click **Graph**, and try these queries:

| Query | What it shows |
|---|---|
| `rate(slmforge_http_requests_total[5m])` | requests per second by route+status |
| `histogram_quantile(0.95, rate(slmforge_http_request_duration_seconds_bucket[5m]))` | p95 latency by route |
| `slmforge_active_runs` | runs currently in `running` state |
| `slmforge_worker_heartbeat_age_seconds` | seconds since each worker's last heartbeat (low = healthy) |
| `slmforge_dataset_count` | total datasets |
| `sum by (status)(slmforge_runs_total)` | total runs grouped by terminal status |
| `rate(slmforge_chat_messages_total[1h])` | chat traffic |

To check what Prometheus is scraping: http://localhost:9090/targets — `slm-forge-api` should show **UP** in green. If it's red, the API container probably isn't healthy yet (run `docker compose logs api`).

### Adding a metric

In `apps/api/routers/metrics.py` declare a new `Counter` / `Gauge` / `Histogram`, increment it in the relevant code path, and Prometheus picks it up at the next scrape (no restart needed).

---

## 5. Loki + Promtail — log reference

### What gets shipped

`observability/promtail-config.yml` has two scrape pipelines:

- **`text` pipeline** tails `./runs/_<worker>.log` files. It parses the leading `HH:MM:SS` timestamp + level token and labels each line with `service=<worker>`, `level=<INFO|WARN|ERROR>`. Body remains free-form.
- **`json` pipeline** tails `./runs/_<worker>.log.json` files (when `SLM_FORGE_LOG_FORMAT=json` is set). It JSON-decodes each line and promotes `service`, `level`, `run_id`, `session_id`, `request_id`, `user_id`, `trace_id` to Loki labels — so you can filter by any of them.

### LogQL — starter queries

In Grafana → **Explore** → datasource **Loki**:

```logql
# All API logs, last 5 min
{service="api"}

# Only errors across every service
{level="ERROR"}

# Everything related to one run (only works in JSON mode)
{run_id="42"}

# Trainer iteration lines (text mode regex)
{service="trainer"} |= "Iter "

# Ratchet errors during an autoresearch session
{service="ratchet", session_id="3"} |= "ERROR"

# Live tail (Grafana auto-refresh)
{service="trainer"} |~ ".*"
```

LogQL supports filters (`|=` contains, `!=` excludes, `|~` regex). See https://grafana.com/docs/loki/latest/query/.

### Switching between text and JSON logs

- **Text** (default): readable in your terminal during `make trainer`, but Promtail can only label by `service` + `level`.
- **JSON**: invisible-to-humans, but every line carries `run_id` / `session_id` / `request_id` labels so cross-service correlation works.

Flip with `SLM_FORGE_LOG_FORMAT=json` in `.env` and restart workers. The dashboard's in-app `<LogPane>` keeps reading the text file so you don't lose live tail.

---

## 6. Correlating a failed run end-to-end

This is the killer use case — when a run fails, you want to see every line across api / trainer / ratchet that's related to it. With JSON logs:

```logql
{run_id="42"}
```

Returns everything from:
- The API line that accepted the `POST /runs` request (`service=api`)
- The ratchet line that orchestrated the session (`service=ratchet`)
- Every trainer line (`service=trainer`)
- The export worker if the run reached export (`service=exporter`)

All sorted by timestamp. Pair with the **Runs Detail** dashboard for the metric series and you have a complete picture.

---

## 7. Practical recipes

### Alert when a worker stops heartbeating

In Grafana: **Alerting → Create alert rule**. PromQL:

```
slmforge_worker_heartbeat_age_seconds > 60
```

Fires when any worker's last heartbeat is > 60s old. Wire it to a contact point (email, Slack webhook).

### Track latency regressions

The **Overview** dashboard has a p95 panel. Hover any data point → **Explore** → see the raw PromQL.

### Pull only the last 50 errors

```logql
{level="ERROR"} | __error__=""
```

(The `__error__=""` filter drops malformed log lines so they don't pollute the result.)

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Grafana shows "No data" on metric panels | Prometheus can't reach the API | `docker compose logs prometheus` — usually a network name typo. Check `prometheus.yml` target is `slm-forge-api:8000`. |
| Loki Explore returns "no data points" | Promtail can't see the log files | `docker compose logs promtail`. The container mounts `./runs` — make sure your workers are actually writing to it (`ls runs/_*.log*`). |
| JSON labels missing in Loki | `SLM_FORGE_LOG_FORMAT` not set, or workers weren't restarted after setting it | `cat runs/_trainer.log.json` should exist; if not, restart the worker. |
| `slm-forge-api` shows DOWN in Prometheus | API container crashed | `docker compose logs api`. Common: missing env var, port collision. |
| Heartbeat age stuck at a huge number | Worker not running | Restart `make trainer` / `make ratchet` / `make exporter` on the host. |
| Grafana login won't accept admin/admin | Volume from a prior run has a different password | `docker compose -f docker-compose.observability.yml down -v` to wipe the Grafana volume (loses your saved dashboards if you customized them). |

---

## 9. Production hardening checklist (when you're ready)

These are NOT done yet — flagged here so future-you remembers:

1. Set a real Grafana admin password via `GF_SECURITY_ADMIN_PASSWORD` env.
2. Add retention to Loki (`compactor.retention_enabled: true`, default config keeps logs forever).
3. Move dashboards into version control (already done — `observability/grafana/dashboards/*.json`).
4. Add Prometheus alerting rules (the file `observability/prometheus.yml` reserves a section for these).
5. Send Loki + Prometheus data to a remote object store (S3 / GCS) once you outgrow the local disk.
6. Add an authenticating reverse proxy (nginx / Caddy + Keycloak — once Phase M is enforcement-on, you can route Grafana through the same OIDC).

That's it. The whole stack is one command up, one command down, and every piece of state lives in mounted volumes you can wipe + restart safely.
