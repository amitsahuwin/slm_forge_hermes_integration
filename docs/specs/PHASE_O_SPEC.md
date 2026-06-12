# Phase O Spec — Trainer backend abstraction (zero behavior change)

> **Status:** approved for implementation · **Date:** 2026-06-12
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` §4–5
> **Prime directive:** after this phase, an MLX training run must behave
> **bit-identically** to today — same YAML config, same subprocess command,
> same metric POSTs, same Run patches, same logs on disk. Phase O only
> creates the seam that Phase Q's CUDA backend will plug into.

---

## 1. Problem

`packages/trainer/runner.py` is a monolith: MLX config generation
(`_write_yaml_config`), command discovery (`_build_mlx_lora_cmd`), stdout
regex metric parsing (`_ITER_TRAIN` / `_ITER_VAL` / `_TEST_LOSS`), and the
orchestration loop all live in one file with no interface between them.
The API has no notion of which backend a run targets. Adding CUDA support
today would mean `if backend == ...` branches everywhere.

## 2. Goals / non-goals

**Goals**

- G1. Introduce a `TrainerBackend` protocol and move all MLX-specific logic
  behind it as `MlxBackend`, with the orchestration in `runner.py` calling
  only the protocol surface.
- G2. Add `trainer_backend` to the `Run` model, API schemas, and SQLite
  forward-migration list. Default `"mlx"` everywhere.
- G3. Add `SLM_FORGE_TRAINER_BACKEND` env var (default `"mlx"`) resolved by
  a backend registry; unknown names fail fast with a clear error.
- G4. Normalize metric parsing into `TrainEvent` objects so any backend's
  output maps to the same `/metrics` POSTs.
- G5. First real test suite under `tests/trainer/` and `tests/api/`
  (the dirs exist but are empty scaffolds).

**Non-goals (later phases)**

- No CUDA backend (Phase Q). No backend-filtered queue claiming or atomic
  lease (Phase R). No catalog changes or mlx-lm upgrade (Phase P). No UI
  changes (Phase S). No change to the canary-eval algorithm.

## 3. New module layout

```
packages/trainer/
├── __main__.py          # worker loop (minor: resolves backend at startup)
├── runner.py            # backend-agnostic orchestration only
└── backends/
    ├── __init__.py      # registry: get_backend(), resolve_backend_name()
    ├── base.py          # TrainEvent, TrainerBackend protocol
    └── mlx.py           # MlxBackend — all logic moved from runner.py
```

## 4. Interfaces

### 4.1 `packages/trainer/backends/base.py`

```python
@dataclass(frozen=True)
class TrainEvent:
    """One normalized metric observation parsed from trainer output."""
    step: int
    name: str    # "train_loss" | "val_loss" | "learning_rate"
                 # | "iters_per_sec" | "tokens_per_sec" | "canary_loss"
    value: float


class TrainerBackend(ABC):
    """Contract every training backend implements."""

    name: ClassVar[str]

    @abstractmethod
    def write_config(self, run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
        """Materialize the backend-native config file; return its path."""

    @abstractmethod
    def build_command(self, config_path: Path) -> list[str] | None:
        """Resolve the subprocess argv, or None if the toolchain is missing."""

    @abstractmethod
    def parse_line(self, line: str) -> list[TrainEvent]:
        """Map one stdout line to zero or more normalized TrainEvents."""

    @abstractmethod
    def missing_toolchain_message(self) -> str:
        """Human-readable error when build_command() returns None."""

    def run_canary_eval(self, run: dict, dataset_dir: Path, adapter_dir: Path,
                        run_dir: Path, env: dict[str, str]) -> float | None:
        """Optional post-train canary evaluation. Default: not supported."""
        return None
```

Design notes: `parse_line` returns a **list** because one MLX train line
yields four metrics (`train_loss`, `learning_rate`, `iters_per_sec`,
`tokens_per_sec`). An abstract base class (not a `typing.Protocol`) so the
default `run_canary_eval` can live on the base.

### 4.2 `packages/trainer/backends/__init__.py`

```python
DEFAULT_BACKEND = "mlx"
ENV_VAR = "SLM_FORGE_TRAINER_BACKEND"

def resolve_backend_name() -> str:
    """Read ENV_VAR, default 'mlx', lowercase/strip; raise ValueError if unknown."""

def get_backend(name: str | None = None) -> TrainerBackend:
    """Return the backend instance for name (or resolve_backend_name())."""
```

Registry is a plain dict `{"mlx": MlxBackend}`. `ValueError` message must
list valid names (acceptance: `pytest` asserts `"mlx"` appears in it).

### 4.3 `packages/trainer/backends/mlx.py`

`class MlxBackend(TrainerBackend)` with `name = "mlx"`. The following move
**verbatim** (same logic, same log lines) out of `runner.py`:

| Old (runner.py) | New (backends/mlx.py) |
|---|---|
| `_ITER_TRAIN`, `_ITER_VAL`, `_TEST_LOSS` regexes | module-level constants |
| `_write_yaml_config()` | `MlxBackend.write_config()` |
| `_build_mlx_lora_cmd()` | `MlxBackend.build_command()` |
| `_run_canary_eval()` | `MlxBackend.run_canary_eval()` |
| `_detect_dataset_format()`, `_safe_val_batches()`, `_count_jsonl()` | module-level helpers in `mlx.py` |

`parse_line()` is new but wraps the existing regexes:

- `_ITER_TRAIN` match → 4 events at the matched step.
- `_ITER_VAL` match → 1 `val_loss` event.
- anything else → `[]`.

`missing_toolchain_message()` returns the exact string currently inlined in
`run_training_job` ("Could not find a working way to invoke mlx_lm.lora…").

### 4.4 `packages/trainer/runner.py` (after refactor)

Keeps: `PROJECT_ROOT` / `DATA_ROOT` / `RUNS_ROOT`, `_patch_run`,
`_post_metric`, dataset-existence guard, subprocess streaming loop, final
Run patch, log files. Changes:

```python
def run_training_job(run: dict, api_url: str,
                     backend: TrainerBackend | None = None) -> None:
    backend = backend or get_backend()
    ...
    config_path = backend.write_config(run, dataset_dir, adapter_dir)
    cmd = backend.build_command(config_path)
    ...
    for raw in proc.stdout:
        ...
        for ev in backend.parse_line(line):
            if ev.name == "train_loss": final_train_loss = ev.value
            if ev.name == "val_loss":   final_val_loss = ev.value
            _post_metric(api_url, run_id, ev.step, ev.name, ev.value)
    ...
    canary_loss = backend.run_canary_eval(run, dataset_dir, adapter_dir, run_dir, env)
```

The `backend` parameter default preserves the existing call signature from
`__main__.py` (which now resolves the backend once at startup and passes it
in, logging `Trainer backend: mlx`).

### 4.5 API & DB (additive only)

- `apps/api/models/run.py` — `trainer_backend: str = "mlx"` (plain `str`,
  not an enum: backend names are worker-side concepts; the API must accept
  a run for a backend the *local* worker doesn't implement).
- `apps/api/routers/runs.py` — `RunCreate.trainer_backend: str = "mlx"`;
  `RunPatch` unchanged (backend is immutable after creation).
- `apps/api/services/db.py` — append `("trainer_backend", "TEXT DEFAULT 'mlx'")`
  to `_RUN_MIGRATIONS` (existing PRAGMA-based forward-migration pattern).
- `.env.example` — document `SLM_FORGE_TRAINER_BACKEND=mlx`.

## 5. Acceptance criteria

- A1 **Config parity.** For a fixed run dict + synthetic dataset dir, the
  YAML produced by `MlxBackend.write_config()` is **key-for-key identical**
  to the pre-refactor `_write_yaml_config()` output (golden dict pinned in
  the test, including `mask_prompt` chat/text variants and `val_batches`
  capping at 0 / floor / 25).
- A2 **Parse parity.** Real mlx-lm log lines map to the exact metric
  stream the old regex block POSTed (names, steps, values, order).
- A3 **Command parity.** With `subprocess.run` monkeypatched to simulate
  (a) modern CLI, (b) legacy module, (c) nothing available,
  `build_command()` returns the same argv shapes / `None` as before.
- A4 **Registry.** `get_backend()` honors `SLM_FORGE_TRAINER_BACKEND`,
  defaults to mlx, raises `ValueError` naming valid backends on junk input.
- A5 **Orchestration parity.** `run_training_job()` driven by a fake
  backend + fake `Popen` + stubbed HTTP makes the same `_patch_run` /
  `_post_metric` calls (status running → completed, final losses, adapter
  path) as today, and writes `training.log`.
- A6 **Model/schema.** `Run().trainer_backend == "mlx"`; `RunCreate`
  accepts and passes through `trainer_backend`; migration list contains
  the new column; fresh-DB `init_db()` + second-boot idempotency both pass
  against a temp SQLite file.
- A7 **No regressions.** Full `pytest` suite green; `ruff check` clean on
  touched files; no public import path elsewhere breaks
  (`packages.trainer.runner.run_training_job` keeps its name and signature
  compatibility).

## 6. Test plan (written before implementation)

```
tests/trainer/test_backend_registry.py   # A4
tests/trainer/test_mlx_backend.py        # A1, A2, A3
tests/trainer/test_runner_orchestration.py  # A5
tests/api/test_run_backend_field.py      # A6 (model + RunCreate + migration)
```

All tests are hermetic: temp dirs for datasets/runs, monkeypatched
`subprocess` and `httpx`, `SLM_FORGE_DB_URL=sqlite:///<tmp>` for the DB
test. No mlx import, no network, no Metal — they must pass on Linux CI as
well as the Mac.

## 7. Rollout

No operator action. Default behavior identical; the env var is optional.
Existing SQLite DBs gain the column on next API boot via `_migrate_runs()`.
Rows created before the migration read `NULL` → treated as `"mlx"` by the
worker (`run.get("trainer_backend") or "mlx"`).

## 8. Out-of-scope risks accepted

- Worker does not yet *filter* queued runs by backend — with only one
  backend registered this is unreachable until Phase R.
- `Run.trainer_backend` validation against a catalog happens in Phase P.
