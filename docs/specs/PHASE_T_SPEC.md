# Phase T Spec — Cross-platform portability (Linux + NVIDIA CUDA, parity with macOS + MLX)

> **Status:** approved for implementation · **Date:** 2026-06-15
> **Owner:** Amit
> **Parent plan:** `docs/MULTI_PLATFORM_TRAINING.md` (the backend-pluggable
> trainer landed in Phases O/Q; the export PEFT branch in Phase Q; the
> remote-claim queue in Phase R; the UI/MCP surfacing in Phases S and the
> MCP-sync change). Phase T closes the last gap: **the operator tooling**
> (Makefile, shell scripts, dependency resolution, default backend) still
> hard-assumes macOS + Homebrew + MLX, so a clean checkout will not bring
> the stack up on a Linux + NVIDIA host.

---

## 1. Problem

Every *application-level* path already supports CUDA — the trainer has an
`mlx`/`cuda` backend registry, the exporter branches to a PEFT merge for
CUDA-trained adapters, the API/DB carries `trainer_backend`, and there is a
`Dockerfile.trainer-cuda` plus a `trainer-cuda` extras group. What is **not**
portable is the glue an operator actually runs on a fresh box:

1. **`make setup` hard-fails on Linux.** It runs `uv sync --all-extras`,
   and the `trainer` extra pins `mlx` / `mlx-lm`, which publish **no Linux
   wheels** → the resolve/build aborts and nothing installs. The error
   messages also tell the user to `brew install …`, which does not exist on
   Linux.
2. **`requires-python = ">=3.12"` vs. a 3.10 system Python.** The GCP box
   ships Python 3.10.12; the code uses 3.11+/3.12 features
   (`datetime.UTC`, PEP 604 unions at runtime). The intended answer is for
   `uv` to provision a managed CPython 3.12 — but `make setup` never tells
   `uv` to do that, and never checks.
3. **The default trainer backend is `mlx` everywhere.** `make trainer`
   runs the MLX worker and `ensure-trainer-installed` probes `import
   mlx_lm`; on Linux that import can never succeed, so the documented
   "run a worker" path dead-ends. The CUDA worker is only reachable via a
   separate, manually-invoked `make trainer-cuda`.
4. **`install_hermes.sh` is macOS-only.** Banner says "Target: macOS Apple
   Silicon"; it uses `launchctl setenv` and `brew services` to run Ollama —
   neither exists on Linux, so Ollama never starts and the keep-alive env
   is never set.
5. **GGUF tooling discovery is Homebrew-only.** `make check-llamacpp` and
   the exporter's `_find_llama_quantize()` only look in
   `/opt/homebrew/bin` and `/usr/local/bin`, and the failure message says
   `brew install llama.cpp`. A Linux user who built llama.cpp from source
   (the documented path) is told it is "not found".
6. **`smoke_model.sh` hard-exits off macOS** (`uname != Darwin → exit 1`)
   and uses the macOS-only `/usr/bin/time -l`.
7. **Helper scripts and docs** (`download_base_model.sh`,
   `start_host_services.sh`, README "Prerequisites") all say
   `brew install …` / "macOS on Apple Silicon".

## 2. Goals / non-goals

**Goals**

- **G1 — One command, any platform.** The *same* `make` targets
  (`setup`, `trainer`, `install-hermes`, `check-llamacpp`, `smoke-model`)
  detect the host (OS + arch + presence of an NVIDIA GPU) and do the right
  thing automatically. No new flags required for the common path; explicit
  overrides remain available.
- **G2 — `uv sync --all-extras` is safe on every platform.** Add PEP 508
  environment markers so MLX installs only on Apple-Silicon macOS and
  bitsandbytes only on Linux. A single resolve works on macOS *and* Linux
  with no per-platform extra selection.
- **G3 — Auto-select the trainer backend.** When
  `SLM_FORGE_TRAINER_BACKEND` is unset, resolve to the platform's
  recommended backend: `mlx` on Apple Silicon, `cuda` on a Linux/NVIDIA
  host, falling back to the existing `mlx` default when detection is
  inconclusive. An explicit env value always wins (unchanged).
- **G4 — Platform-aware Ollama install.** `install_hermes.sh` installs and
  starts Ollama via the official Linux installer + `systemd` (or a
  `nohup ollama serve` fallback), and keeps the macOS Homebrew/launchctl
  path. Keep-alive is configured on both.
- **G5 — Cross-platform GGUF tooling discovery.** `check-llamacpp` and the
  exporter locate `llama-quantize` via `PATH`, common Linux prefixes, and a
  local `scripts/llama_cpp_src/build/bin/` build, with a
  platform-appropriate install hint.
- **G6 — `uv` provisioning + 3.12.** `make setup` checks for `uv` (with a
  platform-correct install hint) and ensures a 3.12 interpreter via
  `uv python install 3.12` / `uv sync` so the 3.12 floor is satisfied
  without touching system Python.
- **G7 — Docs reflect reality.** README prerequisites, `docs/SETUP.md`, and
  `docs/MULTI_PLATFORM_TRAINING.md` gain a Linux/CUDA quickstart.

**Non-goals**

- No change to model/training *semantics* on either backend — Phase T is
  pure portability of the operator tooling and defaults.
- No Windows-native target (WSL2 is covered transitively as "Linux"; a
  first-class Windows path is a follow-up).
- No CPU-only training backend (a Linux box without an NVIDIA GPU still
  resolves to `cuda` and will report a missing toolchain at run time, same
  as today — documented, not silently "fixed").
- Not re-running the full MLX/CUDA training E2E in CI — that stays a manual
  exit check on real hardware.

## 3. Interfaces

### 3.1 `packages/_platform.py` (new, stdlib-only)

A dependency-free detection helper importable from anywhere (must not import
torch/mlx/fastapi, mirroring the backend-import discipline in
`PHASE_O_SPEC.md` §4.1):

```python
def os_name() -> str          # "darwin" | "linux" | "windows"
def machine() -> str          # platform.machine() passthrough, lower-cased
def is_apple_silicon() -> bool
def has_nvidia_gpu() -> bool   # nvidia-smi on PATH OR /proc/driver/nvidia, cached
def recommended_backend() -> str
    # "mlx"  when is_apple_silicon()
    # "cuda" when has_nvidia_gpu()
    # DEFAULT_BACKEND ("mlx") otherwise (inconclusive)
def summary() -> dict          # {os, machine, apple_silicon, nvidia_gpu, backend}
```

`has_nvidia_gpu()` is detection-only and must never raise; a failed/absent
`nvidia-smi` returns `False`. Result is memoized so repeated calls in the
worker are free.

### 3.2 `packages/trainer/backends/__init__.py` (changed)

`resolve_backend_name()` gains auto-detection while preserving every
existing contract:

```
raw = env[SLM_FORGE_TRAINER_BACKEND]
if raw (non-empty):  return _validate(raw.strip().lower())   # explicit wins
else:                return _validate(recommended_backend()) # auto-detect
```

`DEFAULT_BACKEND = "mlx"` is retained as the inconclusive-fallback constant
and as the value `recommended_backend()` returns when neither Apple Silicon
nor an NVIDIA GPU is detected.

### 3.3 Makefile (changed)

New detected variables, evaluated once at parse time:

```
UNAME_S := $(shell uname -s)        # Darwin | Linux
UNAME_M := $(shell uname -m)
HAS_NVIDIA := $(shell command -v nvidia-smi >/dev/null 2>&1 && echo 1 || echo 0)
PLATFORM := mac|linux
TRAINER_BACKEND ?= mlx (mac) | cuda (linux)
UV_INSTALL_HINT := brew install uv | curl -LsSf https://astral.sh/uv/install.sh | sh
```

- `setup` — platform-correct `uv`/`node` hints; `uv python install 3.12 ||
  true`; `uv sync --all-extras` (now safe via markers); backend-aware
  "installed?" confirmation.
- `trainer` — runs with `SLM_FORGE_TRAINER_BACKEND=$(TRAINER_BACKEND)`.
- `ensure-trainer-installed` — probes the **active backend's** toolchain
  (`mlx_lm` for mlx; `torch`+`peft` for cuda).
- `check-llamacpp` — accept `PATH` / Linux prefixes / local source build.
- `trainer-cuda`, `trainer-mlx` — explicit overrides retained.

### 3.4 Scripts (changed)

- `scripts/install_hermes.sh` — OS branch: macOS (brew/launchctl/brew
  services, unchanged) vs Linux (official installer; start via
  `systemctl enable --now ollama` when systemd is present, else background
  `ollama serve`; `OLLAMA_KEEP_ALIVE` via systemd drop-in or exported env).
- `scripts/download_base_model.sh` — platform-correct `uv` hint; default
  model unchanged (callers pass a CUDA-friendly HF id when wanted).
- `scripts/smoke_model.sh` — run on macOS (MLX) **and** Linux (CUDA);
  pick the memory probe per OS (`/usr/bin/time -l` vs `-v`) and the trainer
  invocation per backend; no hard `Darwin` gate.
- `scripts/start_host_services.sh` — platform-aware wording.

### 3.5 `pyproject.toml` (changed)

Add environment markers (no version-floor change; `requires-python`
stays `>=3.12`):

```
trainer       : mlx, mlx-lm   →  ; sys_platform == 'darwin' and platform_machine == 'arm64'
trainer-cuda  : bitsandbytes  →  ; sys_platform == 'linux'
```

`torch` (shared) keeps universal wheels. Result: `uv sync --all-extras`
resolves cleanly on macOS *and* Linux.

### 3.6 `packages/exporter/pipeline.py` (changed)

`_find_llama_quantize()` searches, in order: `PATH` (`shutil.which`),
`scripts/llama_cpp_src/build/bin/llama-quantize` (local source build),
`/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`. The "not found" message
is platform-aware (`brew install llama.cpp` on macOS; build-from-source on
Linux).

## 4. Verification gates (test-first)

Unit tests added **before** implementation:

- **T1 `tests/trainer/test_platform.py`** — `recommended_backend()` returns
  `mlx` when `is_apple_silicon()` is patched true; `cuda` when
  `has_nvidia_gpu()` is patched true and Apple Silicon false; the
  `DEFAULT_BACKEND` fallback when both are false. `has_nvidia_gpu()` never
  raises when `nvidia-smi` is absent. `os_name()`/`machine()` are stable.
- **T2 `tests/trainer/test_backend_registry.py`** (updated) — explicit env
  var still wins (case/whitespace-insensitive); unknown value still raises
  naming valid backends; **unset** env now resolves to
  `recommended_backend()` (asserted via monkeypatched detection), and the
  inconclusive case resolves to `DEFAULT_BACKEND == "mlx"`.
- **T3 `tests/exporter/test_llama_quantize_discovery.py`** — a
  `llama-quantize` placed in a fake local-build dir is found; absence
  yields `None`; `PATH` hits win. (Skips if `httpx` unavailable, matching
  the suite's optional-dependency convention.)

Acceptance criteria:

- **A1** New + updated unit tests green; pre-existing suite shows **no
  regressions** (`uv run pytest` on a 3.12 interpreter — the canonical gate
  on the target host; the platform/registry subset additionally runs on a
  bare 3.10 because it is stdlib-only).
- **A2** `uv sync --all-extras` completes on Linux x86_64 **and** macOS
  arm64 (markers verified by inspecting the resolved set; MLX absent on
  Linux, bitsandbytes absent on macOS).
- **A3 (manual, Linux+CUDA box)** From a clean checkout: `make setup` →
  `make install-hermes` → `make dev` → `make trainer` starts the **cuda**
  worker with no env override; a LoRA run trains and an export produces a
  GGUF.
- **A4 (manual, macOS)** Same targets still select `mlx` and behave exactly
  as before — zero behavioural change on the original platform.
- **A5** `ruff check` clean on touched Python files.

## 5. Rollout & compatibility

Additive and backward-compatible. macOS keeps `mlx` as its resolved
default and its Homebrew install paths. The only intentional behaviour
change: on a Linux/NVIDIA host with no `SLM_FORGE_TRAINER_BACKEND`, the
default backend is now `cuda` instead of an unusable `mlx` — strictly an
improvement. Explicit `SLM_FORGE_TRAINER_BACKEND` overrides are unchanged,
so existing CI, Docker, and operator muscle-memory keep working.

## 6. Known follow-ups

- First-class Windows-native target (today: WSL2 = "linux").
- CPU-only fallback backend for GPU-less Linux smoke tests.
- Containerize the host workers fully (the CUDA trainer Docker image exists;
  a compose profile that runs it against the queue would remove the last
  "run it on the host" step).
