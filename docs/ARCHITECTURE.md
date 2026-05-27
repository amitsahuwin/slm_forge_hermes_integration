# Architecture

> Full plan lives in the project's design doc. This file is the concise reference.

## Key decisions

| Concern | Choice | Why |
|---|---|---|
| Orchestration | docker-compose (UI + API only) | K8s/ArgoCD are cluster tools; we have one machine. |
| Trainer location | **Host macOS, NOT Docker** | Apple Metal/MLX is not accessible from Linux containers. |
| Training engine | **MLX-LM** | Fastest path on Apple Silicon, ~3× PyTorch-MPS on M3 Max. |
| Job queue | **Huey + SQLite** | No Redis/RabbitMQ container; one less moving part. |
| DB | SQLite via SQLModel | Single-user local tool; zero-config. |
| Frontend | React 19 + Vite + Tailwind | Modern, fast, low-config. |
| Backend | FastAPI + SSE | Live log/metric streaming over EventSource. |
| Agent | Hermes Agent (sibling process) | Loose coupling via shared SQLite + filesystem. |
| Agent LLM (default) | Ollama + qwen2.5-coder:14b | Local, free, no rate limits. |
| Agent LLM (fallback) | Groq qwen-2.5-coder-32b | Fast, free tier. |
| Python deps | uv + pyproject.toml | 10-100× faster than pip; lockfile. |
| Python version | 3.12+ | Modern stdlib + uv default. |
| CI | GitHub Actions | Lint, typecheck, build on push. |

## Component map

```
  Browser ──HTTP──► UI (React, Docker)
                     │
                     │ HTTP + SSE
                     ▼
                    API (FastAPI, Docker)
                     │
                     │ enqueue (Huey + SQLite)
                     ▼
                    SQLite ◄────────── reads/writes ──── Trainer (HOST, MLX-LM)
                     ▲                                       │
                     │                                       │ requests mutation
                     │                                       ▼
                     │                                   Ratchet (HOST, Python)
                     │                                       │
                     │                                       │ asks for next config
                     │                                       ▼
                     └──────── writes skills ──── Hermes Agent (HOST, CLI)
                                                              │
                                                              │ uses
                                                              ▼
                                                          Ollama (HOST, :11434)
```

## The autoresearch loop (Phase 2)

```
baseline → Hermes proposes mutation → train → eval
                                              │
                       improved ─────────────►├──► git commit ─┐
                       worse/same ───────────►├──► git reset   │
                       error ────────────────►├──► Hermes fixes│
                                              │                │
                                              └────────────────┘
                                              (until plateau or budget)
```
