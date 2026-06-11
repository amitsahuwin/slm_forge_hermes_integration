# SLM-Forge — project memory for Claude

This file captures per-project preferences that should apply to every
session. Things added under `## Preferences` are sticky requests from
Amit; respect them in every response unless overridden.

---

## Preferences

### Commit messages

For any non-trivial change set, deliver a **proper commit message as a
Markdown file** (typically `COMMIT_MESSAGE.md` at the project root, or
`docs/commits/<short-slug>.md` for smaller PRs).

It must read like a GitHub release page when rendered on the commit page:

- Top-level `#` heading naming the change.
- Block-quote summary directly under the heading.
- `## Highlights` bullets.
- A phase / change-set index table when multiple sub-areas are involved.
- File-by-file "what's new" sections grouped by area (backend / frontend / ops / docs).
- Bug-fixes-folded-in table (Bug · Root cause · Fix).
- Compatibility & rollout notes, including any opt-in env vars and Docker compose profiles.
- Testing checklist (what was run + the expected outputs).
- Known follow-ups.

Match the structure of the existing `COMMIT_MESSAGE.md` in the repo — that's the canonical template.

Always lead the response that accompanies the commit with a one-sentence summary
of what shipped, then present the file via `mcp__cowork__present_files`.

### Explanations

Always give a proper explanation alongside the commit. Crisp, technical, no fluff;
prefer prose paragraphs over bulleted lists when the goal is exposition. Use
tables only for things that are inherently tabular (matrices, comparisons,
rollout matrices).

---

## Stack quick-reference

- **Backend**: FastAPI + sqlmodel + sse-starlette, Python 3.12.
- **Frontend**: React 19 + Vite + Tailwind + react-router 7.
- **Workers**: trainer / ratchet / exporter — host processes (Apple Silicon MLX).
- **Auth**: Keycloak realm `slm-forge` + OPA Rego policies; service-token bypass for workers.
- **Observability**: structured JSON logs + Prometheus + Loki + Promtail + Grafana.
- **Inference**: Ollama (`qwen3:30b-a3b` for Hermes).

## Make targets that matter

```
make dev / dev-d              # core stack
make trainer / ratchet / exporter   # host workers (auto-export SLM_FORGE_LOG_FORMAT=json)
make auth ENABLED=true|false  # Keycloak + OPA on/off
make obs-up / obs-down        # observability overlay
make mcp-up                   # MCP server for Claude Desktop / Cursor / Claude Code
make opa-test                 # 18 Rego unit tests
```

## Documentation index

See `README.md` for the canonical entry point. Key supporting docs:

- `docs/PLAN.md` — phase-by-phase plan
- `docs/AUTH.md` — Keycloak + OPA operator runbook
- `docs/OBSERVABILITY_SETUP.md` — Prometheus / Grafana / Loki / Promtail setup
- `docs/MCP_SETUP.md` — MCP client integration
- `docs/TOOL_CALLING_GUIDE.md` — tool calling on fine-tuned GGUF models
- `docs/MARKET_ANALYSIS.md` — competitor study
- `COMMIT_MESSAGE.md` — the canonical commit-message template
