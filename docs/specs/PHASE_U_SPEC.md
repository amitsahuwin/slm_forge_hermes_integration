# Phase U Spec — Backend parity for autoresearch experiments + gated-model unblock

> **Status:** approved for implementation · **Date:** 2026-06-18
> **Owner:** Amit
> **Parent plan:** Phase O introduced `Run.trainer_backend`; Phase R added the
> backend-aware claim queue; Phase S surfaced the v2 catalog + per-backend model
> filtering on the **New Run** page; Phase T made the operator tooling
> cross-platform. Phase U closes two gaps that surface on a Linux + NVIDIA box.

---

## 1. Problem

### 1.1 Experiments silently ignore the training backend

The **New Run** page (`NewRun.tsx`) is backend-aware end-to-end: it reads the
platform's `default_backend`, shows a **Training backend** selector, filters the
model dropdown per-backend, and sends `trainer_backend` to `POST /runs`. The
**autoresearch experiment** path has none of this:

- `TrainingSession` (model **and** `SessionCreate` schema) has **no
  `trainer_backend` field**.
- The ratchet loop (`packages/ratchet/loop.py`) builds its per-iteration
  `run_payload` **without** `trainer_backend`, so every spawned `Run` falls back
  to the model default `"mlx"` (`apps/api/models/run.py`).
- On a Linux/CUDA host there is no MLX worker, so the backend-aware claim
  endpoint (`POST /runs/claim` filtering by `backend`) never matches those runs.
  They stay `queued` forever — the experiment appears to "do nothing."

`NewExperiment.tsx` was edited to pick a platform-aware default *model id*, but a
model id is not a backend; nothing carries the backend through the
session → ratchet → run pipeline, so the change has no effect.

### 1.2 Gated HF repos 401 because the worker never loads `.env`

Selecting a gated checkpoint (e.g. `google/gemma-3-4b-it`) for a CUDA run fails:

```
OSError: You are trying to access a gated repo. ... 401 Client Error.
```

The CUDA subprocess inherits `os.environ` from the trainer worker
(`packages/trainer/runner.py`), and `HF_TOKEN` **is** present in the project
`.env`. But unlike `ratchet/hermes_bridge.py`, the **trainer worker entrypoint
never calls `load_dotenv`**, so `HF_TOKEN` is absent from the worker's
environment and the gated download is unauthenticated. Secondary gaps: the
catalog only flags Llama as gated (not the Gemma variants), and the 401 reaches
the UI as a raw, non-actionable `OSError`.

---

## 2. Requirements

### R1 — `trainer_backend` on sessions
- `TrainingSession` SQLModel gains `trainer_backend: str = "mlx"` (plain str,
  mirroring `Run`; the API must accept backends a given deployment may not run).
- `db.py` gains a forward-migration so pre-Phase-U `sessions` tables get the
  column with `DEFAULT 'mlx'`; `init_db()` is idempotent.
- `SessionCreate` gains `trainer_backend: str = "mlx"` and is validated with the
  same `validate_run_request(base_model, trainer_backend)` used by `create_run`,
  returning HTTP 422 on a mismatch/broken/uncataloged combo.

### R2 — Ratchet propagation
- `run_session` reads `session["trainer_backend"]` and includes it in every
  `run_payload`, so each iteration is queued for the session's backend.
- Backward compatible: a session dict missing the key defaults to `"mlx"`.

### R3 — NewExperiment backend parity
- `api.ts`: `TrainingSession` type gains `trainer_backend: TrainerBackendName`;
  `CatalogBackendVariant` gains `gated?: boolean`.
- `NewExperiment.tsx` mirrors `NewRun.tsx`:
  - default backend from `platform.default_backend`;
  - a **Training backend** `<select>` (mlx / cuda) with the same options/tips;
  - the base-model dropdown is driven by the **v2 catalog filtered to the
    selected backend**, disabling `broken` variants, and showing status (and a
    "gated" hint) badges;
  - switching backend remaps to the same logical model when possible;
  - `createSession` is called with `trainer_backend`.
  - The Ask-Hermes recommendation panel is preserved.

### R4 — Gated-model unblock
- The trainer worker entrypoint (`packages/trainer/__main__.py`) loads
  `.env` from the project root at startup (same guarded `load_dotenv` pattern as
  `hermes_bridge.py`, `override=False`) so `HF_TOKEN` reaches the training
  subprocess.
- `BackendVariant` gains `gated: bool = False`; the `google/*` (Gemma) and
  `meta-llama/*` (Llama) checkpoints are marked `gated=True` with a note that a
  one-time license acceptance + `HF_TOKEN` is required.
- `cuda_train.py` wraps model/tokenizer loading and, on an auth/gated failure
  (401 / "gated repo"), emits an actionable error naming the repo, the license
  URL, and the `HF_TOKEN` requirement before re-raising.

### R5 — No regressions
- The full existing pytest suite stays green; the frontend type-checks/builds.

---

## 3. Interfaces

```python
# apps/api/models/session.py
class TrainingSession(SQLModel, table=True):
    ...
    trainer_backend: str = "mlx"   # Phase U

# apps/api/routers/sessions.py
class SessionCreate(BaseModel):
    ...
    trainer_backend: str = "mlx"   # validated via validate_run_request()

# apps/api/services/model_catalog.py
class BackendVariant(BaseModel):
    ...
    gated: bool = False            # Phase U
```

```python
# packages/ratchet/loop.py — run_payload
run_payload = {
    "dataset": session["dataset"],
    "base_model": session["base_model"],
    "method": session["method"],
    "trainer_backend": session.get("trainer_backend", "mlx"),  # Phase U
    **hp, "grad_checkpoint": False, "seed": 0,
}
```

```ts
// apps/web/src/lib/api.ts
export type CatalogBackendVariant = { ...; gated?: boolean };
export type TrainingSession = { ...; trainer_backend: TrainerBackendName };
```

---

## 4. Acceptance criteria

1. `TrainingSession(...).trainer_backend == "mlx"`; `trainer_backend="cuda"`
   round-trips through `TrainingSession(**SessionCreate(...).model_dump())`.
2. `"trainer_backend"` is in the session migration list; a legacy `sessions`
   table gains the column after `_migrate_sessions()`.
3. `POST /sessions` with a cuda base_model + `trainer_backend="cuda"` succeeds;
   a backend/model mismatch returns 422.
4. `run_session` issues `create_run` payloads whose `trainer_backend` equals the
   session's backend (verified with a stubbed API in the ratchet test).
5. `find_by_model_id("google/gemma-3-4b-it")` → variant with `gated is True`;
   `Qwen/Qwen2.5-3B-Instruct` → `gated is False`.
6. Importing `packages.trainer.__main__` invokes `load_dotenv` against the
   project-root `.env` (HF_TOKEN reaches `os.environ`).
7. New Experiment page renders a Training-backend selector defaulting to the
   detected backend and a per-backend model list; submitting posts
   `trainer_backend`.
8. Full pre-existing pytest suite + new tests pass; `tsc`/build clean.

---

## 5. Out of scope

- Accepting the Gemma/Llama licenses on Hugging Face (one-time manual step per
  account) and provisioning the `HF_TOKEN` value itself.
- Per-iteration backend switching within a single session (a session pins one
  backend for all its runs).
