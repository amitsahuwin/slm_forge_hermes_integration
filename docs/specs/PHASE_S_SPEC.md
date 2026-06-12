# Phase S Spec — UI: backend picker, catalog v2 hints, claim visibility

> **Status:** approved for implementation · **Date:** 2026-06-12
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` §5 (final phase)
> **Depends on:** P (`/models/v2`), R (`trainer_backend`/`claimed_by` on Run)

---

## 1. Problem

The backend plumbing is invisible: the New Run form still consumes the
flat legacy catalog, cannot target the CUDA backend at all, and — a real
bug — hardcodes its default model to the **broken** gemma-3n bf16
checkpoint (`NewRun.tsx` line 14), which Phase P's validation now rejects
with a 422. Run lists/detail don't show which backend executed a run or
which worker claimed it.

## 2. Goals / non-goals

**Goals**

- G1. **`api.ts` types/client**: `Run` gains `trainer_backend`,
  `claimed_by`, `claimed_at`; new `CatalogBackendVariant` /
  `CatalogModelV2` types + `api.listModelsV2()`.
- G2. **New Run form**:
  - Backend picker (`mlx` = "Apple Silicon (this Mac)", `cuda` =
    "NVIDIA GPU worker"), default `mlx`.
  - Model dropdown driven by `/models/v2`, showing only models with a
    variant for the chosen backend; option value = that variant's
    `model_id`; `broken` variants rendered disabled with a ⚠ suffix.
  - Default selection = the first non-broken variant (fixes the stale
    hardcoded gemma-3n default).
  - Hint block under the dropdown: variant notes + memory line
    (`needs ≥ {min_memory_gb} GB · {status}`) with an amber "untested"
    badge and the worker-startup tip per backend (`make trainer` vs
    `make trainer-cuda` / Docker).
  - Switching backend re-maps the selection to the same logical model's
    other variant when it exists, else the default.
  - Payload includes `trainer_backend`.
- G3. **Runs list**: small backend chip (`mlx` zinc / `cuda` violet)
  next to the model name.
- G4. **Run detail**: `trainer_backend` and `claimed_by` rows in the
  config table (claimed_by only when present).
- G5. **API serialization guard** (Python test): the `Run` response model
  exposes `trainer_backend`, `claimed_by`, `claimed_at` — pins the
  contract the frontend types now rely on.

**Non-goals:** session/experiment backend selection (ratchet experiments
stay mlx — follow-up), worker heartbeat dashboard tiles, a JS unit-test
harness (none exists in the repo; introducing vitest is its own decision
— recorded as a follow-up, not smuggled into this phase).

## 3. Verification gates (test-first, adapted for the FE)

The repo has **no JS test runner**, so the frontend gate is:

1. **`tsc --noEmit`** (strict typecheck, `apps/web`) — must pass in CI
   *and* the sandbox.
2. The **Python suite** (114 tests) + the new serialization test — green.
3. **Manual exit checklist** on the Mac (vite build + the flows below).

Acceptance criteria:

- A1. `tsc --noEmit` clean with the new types and pages.
- A2. Python: `Run` API model serializes the three new fields (test).
- A3. Manual — New Run with backend `mlx`: dropdown shows 9 models,
  gemma-3n disabled with ⚠, default = Qwen 2.5 3B, hint shows memory +
  status, run submits and trains exactly as before.
- A4. Manual — switching to `cuda` swaps the model ids to full-precision
  HF repos (e.g. `google/gemma-4-E4B-it`), tip switches to
  `make trainer-cuda`, submitted run sits queued until a CUDA worker
  claims it.
- A5. Manual — Runs list shows the backend chip; Run detail shows
  backend + `claimed_by` (e.g. `Amits-MacBook-Pro.local:4242`).

## 4. Files

| File | Change |
|---|---|
| `apps/web/src/lib/api.ts` | Run fields, v2 catalog types, `listModelsV2` |
| `apps/web/src/pages/NewRun.tsx` | Backend picker + v2-driven model select + hints |
| `apps/web/src/pages/Runs.tsx` | Backend chip in the table row |
| `apps/web/src/pages/RunDetail.tsx` | Backend + claimed_by rows |
| `tests/api/test_run_backend_field.py` | + serialization-contract test |

## 5. Rollout

Pure frontend + one test; Vite HMR picks it up. The legacy `/models`
endpoint stays untouched (NewExperiment still consumes it — follow-up).
No API or DB changes.
