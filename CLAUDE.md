# SLM-Forge — project memory for Claude

This file captures per-project preferences that should apply to every
session. Things added under `## Preferences` are sticky requests from
Amit; respect them in every response unless overridden.

---

## Preferences

### Commit messages — non-negotiable

**At the end of every task that touched code, ALWAYS rewrite
`COMMIT_MESSAGE.md` with a beautifully-formatted GitHub release-style
markdown summary.** This is a hard requirement — not "if you have time".
The user often amends with this file (`git commit --amend -F
COMMIT_MESSAGE.md`), so the file must reflect the latest change set, not
a stale older one.

For any non-trivial change set, the commit message file goes at
`COMMIT_MESSAGE.md` at the project root (or `docs/commits/<short-slug>.md`
for smaller PRs).

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

### Spec-driven development — non-negotiable

**Always follow spec-driven development, strictly sequential (no parallel
phases):**

1. **Spec first.** Before writing any code for a phase, write the full
   spec to `docs/specs/<PHASE>_SPEC.md` (requirements, interfaces,
   acceptance criteria).
2. **Tests second.** Write the test cases for that spec before the
   implementation.
3. **Code third.** Implement against the spec.
4. **Gate on green.** Only proceed past a phase when its test cases pass
   (plus the pre-existing suite — no regressions).
5. **Commit gate.** When a phase completes: rewrite `commit_message.md`
   (per the rule above), then `git add` → `git commit` → `git push`.
   Only after the push move to the next phase.

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
