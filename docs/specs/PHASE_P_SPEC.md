# Phase P Spec — Model catalog v2 (backend-aware) + dependency floor + validation

> **Status:** approved for implementation · **Date:** 2026-06-12
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` §4.3, §5
> **Depends on:** Phase O (merged: `TrainerBackend`, `Run.trainer_backend`)

---

## 0. Research correction to the parent plan

The plan assumed "upgrade mlx-lm past 0.31.x" unlocks Gemma. Verified
2026-06-12: **0.31.3 (2026-04-22) is the latest mlx-lm on PyPI**, and the
repo already locks it. Inspection of the installed package shows the
architecture modules are *already present*: `gemma3.py`, `gemma3_text.py`,
`gemma3n.py`, `gemma4.py`, `gemma4_text.py`, `mistral3.py` (Mistral 7B
v0.3 loads via `llama`). Consequences:

- There is no version bump to make. Instead we **raise the floor** in
  `pyproject.toml` (`mlx>=0.31.2`, `mlx-lm>=0.31.3`) so older envs can't
  silently miss Gemma 3/3n/4 support.
- The `gemma-3n-E2B-it-bf16` `KeyError` in `sanitize()` is a
  model-checkpoint bug on the current release, not stale-version damage.
  The catalog must therefore carry **per-entry status** (`stable` /
  `untested` / `broken`) rather than prose warnings, and the Mac
  smoke-test matrix (§6) is what promotes entries to `stable`.

## 1. Problem

The catalog (`apps/api/routers/models.py`) is a flat, MLX-only,
4-entry list whose default model is literally marked broken; `RunCreate`
accepts any `base_model` string silently; `RunCreate` and the Hermes chat
agent both *default* to the broken gemma-3n checkpoint. There is no notion
of which backend can train which model, and no memory guidance.

## 2. Goals / non-goals

**Goals**

- G1. **Catalog v2 as a service module** (`apps/api/services/model_catalog.py`):
  logical models with per-backend physical IDs, memory hints, and status.
- G2. **Wider MLX lineup:** Gemma 3 4B/12B, Gemma 4 E2B/E4B, Mistral 7B
  v0.3 — all as 4-bit `mlx-community` checkpoints — alongside the existing
  Qwen 2.5 3B/7B and Llama 3.2 3B. Forward-looking `cuda` IDs recorded now
  (full-precision HF repos) so Phase Q needs zero catalog work.
- G3. **API compatibility:** `GET /api/v1/models` keeps its exact legacy
  shape (the React `NewRun`/`NewExperiment` pages read `hf_id`, `label`,
  `notes`); new `GET /api/v1/models/v2` exposes the full entries.
- G4. **Validation:** `POST /runs` rejects (a) `base_model` not in the
  catalog, (b) `trainer_backend` the chosen model doesn't support —
  HTTP 422 with an actionable message. Escape hatch:
  `SLM_FORGE_ENFORCE_CATALOG=false` (default `true`).
- G5. **Default-model fix (bug):** `RunCreate.base_model` and the chat
  agent's `start_experiment` default switch from the broken gemma-3n bf16
  to the catalog default (Qwen 2.5 3B 4-bit).
- G6. **Dependency floor:** `mlx>=0.31.2`, `mlx-lm>=0.31.3` in
  `pyproject.toml` optional-dependency group `trainer`.
- G7. **Smoke-test matrix tooling:** `scripts/smoke_model.sh` +
  `make smoke-model MODEL=<key>` runs a 30-iter LoRA job on the Mac and
  reports wall time + peak memory, so §2 of the plan doc gets measured
  numbers and entries get promoted from `untested` → `stable`.

**Non-goals:** CUDA execution (Phase Q); UI backend picker (Phase S);
queue claiming (Phase R); fixing gemma-3n upstream.

## 3. Interfaces

### 3.1 `apps/api/services/model_catalog.py` (new)

```python
class BackendVariant(BaseModel):
    model_id: str                  # physical checkpoint for this backend
    min_memory_gb: float           # planning estimate; measured by smoke tests
    quant: str | None = None       # e.g. "4bit" (mlx) | "nf4" (cuda)
    status: str = "untested"       # "stable" | "untested" | "broken"
    notes: str = ""

class CatalogModel(BaseModel):
    key: str                       # logical id, e.g. "gemma-4-e4b-it"
    label: str
    family: str                    # qwen | llama | gemma | mistral
    size_params: str               # human label, e.g. "8B raw / ~4B effective"
    recommended_method: str = "lora"
    backends: dict[str, BackendVariant]   # "mlx" | "cuda"

CATALOG_V2: list[CatalogModel]
DEFAULT_MODEL_KEY = "qwen2.5-3b-instruct"

def get_model_by_key(key: str) -> CatalogModel | None
def find_by_model_id(model_id: str) -> tuple[CatalogModel, str] | None
    # matches any backend's model_id; returns (model, backend_name)
def allowed_model_ids() -> set[str]          # union across backends
def default_model_id(backend: str = "mlx") -> str
def validate_run_request(base_model: str, trainer_backend: str) -> str | None
    # returns an error message, or None if OK / enforcement disabled
```

Enforcement reads `SLM_FORGE_ENFORCE_CATALOG` at call time (testable via
monkeypatch), default `"true"`; any of `0/false/no/off` disables.

### 3.2 Catalog contents (launch set)

| key | family | MLX checkpoint (4-bit unless noted) | mlx status | mlx min GB | CUDA checkpoint |
|---|---|---|---|---|---|
| `qwen2.5-3b-instruct` *(default)* | qwen | `mlx-community/Qwen2.5-3B-Instruct-4bit` | stable | 6 | `Qwen/Qwen2.5-3B-Instruct` |
| `llama-3.2-3b-instruct` | llama | `mlx-community/Llama-3.2-3B-Instruct-4bit` | stable | 6 | `meta-llama/Llama-3.2-3B-Instruct` |
| `qwen2.5-7b-instruct` | qwen | `mlx-community/Qwen2.5-7B-Instruct-4bit` | stable | 9 | `Qwen/Qwen2.5-7B-Instruct` |
| `gemma-3-4b-it` | gemma | `mlx-community/gemma-3-4b-it-4bit` | untested | 7 | `google/gemma-3-4b-it` |
| `gemma-3-12b-it` | gemma | `mlx-community/gemma-3-12b-it-4bit` | untested | 16 | `google/gemma-3-12b-it` |
| `gemma-4-e2b-it` | gemma | `mlx-community/gemma-4-E2B-it-4bit` | untested | 10 | `google/gemma-4-E2B-it` |
| `gemma-4-e4b-it` | gemma | `mlx-community/gemma-4-E4B-it-4bit` | untested | 12 | `google/gemma-4-E4B-it` |
| `mistral-7b-instruct-v0.3` | mistral | `mlx-community/Mistral-7B-Instruct-v0.3-4bit` | untested | 9 | `mistralai/Mistral-7B-Instruct-v0.3` |
| `gemma-3n-e2b-it` | gemma | `mlx-community/gemma-3n-E2B-it-bf16` | **broken** | 10 | `google/gemma-3n-E2B-it` |

`broken` entries stay listed (visibility + the legacy UI already shows the
warning label) but **fail validation** with a message naming the status.
`untested` entries pass validation; the smoke matrix flips them to
`stable` with measured `min_memory_gb`.

### 3.3 `apps/api/routers/models.py` (rewritten as views)

- `GET /api/v1/models` → legacy `list[BaseModelInfo]` **derived** from
  CATALOG_V2's mlx variants (`hf_id=variant.model_id`, label gains a
  "⚠ broken" suffix when status=broken — same UX as today). Field set is
  frozen: `hf_id, label, family, size_params, recommended_method, notes`.
- `GET /api/v1/models/v2` → `list[CatalogModel]` (full entries).

### 3.4 `apps/api/routers/runs.py`

`create_run` calls `validate_run_request(payload.base_model,
payload.trainer_backend)` and raises `HTTPException(422, msg)` on error.
`RunCreate.base_model` default becomes
`"mlx-community/Qwen2.5-3B-Instruct-4bit"`.

### 3.5 Workers / agents

`packages/chat_agent/tools.py` `start_experiment` default `base_model`
switches to the same Qwen default. No other worker change: ratchet copies
`base_model` from its session row.

### 3.6 Ops

- `pyproject.toml`: trainer extras floor → `mlx>=0.31.2`, `mlx-lm>=0.31.3`.
- `.env.example`: document `SLM_FORGE_ENFORCE_CATALOG=true`.
- `scripts/smoke_model.sh` (mac-only, bash): resolves a catalog key to its
  mlx model id via the API (`/api/v1/models/v2`), writes a tiny throwaway
  chat dataset, runs `mlx_lm lora` for 30 iters with `/usr/bin/time -l`,
  and prints elapsed time, peak RSS (GB), and final train loss.
- `Makefile`: `make smoke-model MODEL=gemma-4-e4b-it`.

## 4. Acceptance criteria

- A1. Catalog integrity: all 9 keys present and unique; every entry has an
  `mlx` variant; `mlx` model_ids unique; default key exists with a stable
  mlx variant; every entry has a `cuda` variant (Phase Q readiness).
- A2. Legacy view: `list_models()` returns objects with exactly the frozen
  legacy field set; gemma-3n keeps a visible broken marker in its label;
  ordering puts the default model first.
- A3. Validation: cataloged id + `mlx` → accepted; junk id → 422 message
  listing how to see valid models; `broken` status id → 422 naming the
  status; cataloged id + unsupported backend → 422; enforcement env
  `false` → everything accepted.
- A4. `create_run` integration: POSTing (via direct call with a temp-DB
  session) a junk model raises HTTPException 422 and persists nothing; a
  valid request persists with `trainer_backend="mlx"`.
- A5. Defaults: `RunCreate().base_model` == catalog default mlx id;
  catalog default's status is `stable`.
- A6. Floors raised in `pyproject.toml` (assert via parsing the file).
- A7. Full suite green (Phase O's 32 tests + new ones); ruff clean on
  touched files.

## 5. Test plan

```
tests/api/test_model_catalog.py      # A1, A3 (unit: catalog + validation)
tests/api/test_models_endpoint.py    # A2 (legacy + v2 views)
tests/api/test_run_validation.py     # A4, A5 (create_run with temp DB)
tests/api/test_dependency_floor.py   # A6 (pyproject parse)
```

Hermetic as before: no network, no mlx import, temp SQLite.

## 6. Mac-side verification (manual, post-merge)

Run `make smoke-model MODEL=<key>` for: `gemma-3-4b-it`, `gemma-3-12b-it`,
`gemma-4-e2b-it`, `gemma-4-e4b-it`, `mistral-7b-instruct-v0.3`. Record
peak GB / tokens-sec into `docs/MULTI_PLATFORM_TRAINING.md` §2 and flip
catalog statuses to `stable` (or `broken` with the error in notes). This
is the phase's exit checklist item that cannot run in CI.

## 7. Rollout

- Catalog enforcement defaults **on**; deployments training uncataloged
  models set `SLM_FORGE_ENFORCE_CATALOG=false`.
- Existing rows are untouched (validation is create-time only).
- The default-model change affects only requests that omitted
  `base_model` — previously those would have hit the broken gemma-3n.
- Gated HF repos (Gemma, Llama, Mistral) need a one-time
  `huggingface-cli login` on the Mac.
