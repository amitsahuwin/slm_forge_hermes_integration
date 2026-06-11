# SLM-Forge Observability

Phase L bolts a real telemetry stack onto SLM-Forge: structured JSON
logs, Prometheus metrics, and a Loki + Grafana overlay. Everything is
opt-in — the day-to-day `make dev` workflow is unchanged.

## Start the stack

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  up -d
```

Services:

| URL | Service |
|---|---|
| http://localhost:8000 | SLM-Forge API |
| http://localhost:5173 | SLM-Forge Web |
| http://localhost:3001 | **Grafana** (admin / admin) |
| http://localhost:9090 | Prometheus |
| http://localhost:3100 | Loki (push API) |

Grafana boots with three provisioned dashboards under the `SLM-Forge`
folder:

* **SLM-Forge Overview** — HTTP request rate, p95 latency, worker
  heartbeat ages, active-run + dataset counts.
* **SLM-Forge Runs** — `train_loss` / `val_loss` / `canary_loss`
  extracted from trainer log lines via LogQL, filterable by `run_id`.
* **SLM-Forge Logs** — three starter explorer panels: API logs, trainer
  iteration lines, all errors.

## Structured JSON logging

Set `SLM_FORGE_LOG_FORMAT=json` in the environment of any worker (API,
trainer, exporter, ratchet) to switch from the human-readable text format
to one-JSON-per-line. Every line carries:

```json
{"ts": "2026-06-11T15:00:00.123+00:00", "level": "INFO", "service": "trainer",
 "logger": "trainer.worker", "msg": "Iter 10: train_loss=0.42",
 "run_id": "42", "request_id": "ab12...", "user_id": "anonymous"}
```

Correlation IDs are propagated through `contextvars`:

* `request_id` + `user_id` — bound per HTTP request by
  `RequestContextMiddleware` (also echoed via the `X-Request-ID` response
  header).
* `run_id` — bound by the trainer / exporter `__main__` around the inner
  job call.
* `session_id` — bound by the ratchet `__main__` around `run_session`.

Anything you log while the contextvar is set picks it up automatically.

## Prometheus metrics

`GET /metrics` on the API container returns the standard text exposition.
Available metrics:

| Metric | Type | Labels |
|---|---|---|
| `slmforge_http_requests_total` | Counter | `method`, `route`, `status` |
| `slmforge_http_request_duration_seconds` | Histogram | `method`, `route` |
| `slmforge_runs_total` | Counter | `status` |
| `slmforge_active_runs` | Gauge | — |
| `slmforge_worker_heartbeat_age_seconds` | Gauge | `worker` |
| `slmforge_dataset_count` | Gauge | — |
| `slmforge_chat_messages_total` | Counter | `role` |

Gauges are refreshed on-scrape by sampling SQLite — no background tasks.

## Promtail mounts

Promtail tails `./runs/_*.log` (text) and `./runs/_*.log.json` (JSON)
read-only, plus the docker socket so the API container's stdout becomes
the `service="api"` Loki stream.

## Tearing it down

```bash
docker compose -f docker-compose.observability.yml down
```

Volumes are ephemeral — restart and you start clean.
