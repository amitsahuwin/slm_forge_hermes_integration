# packages/trainer — host trainer worker

Runs on the host (GPU/Metal access). Entrypoint: `python -m packages.trainer` (via `make trainer` / `trainer-mlx` / `trainer-cuda`).

## Layout
- `__main__.py` — worker loop (`main()`). Loads `.env`, claims runs via API filtered by `SLM_FORGE_TRAINER_BACKEND`, sets up JSON logging.
- `runner.py` — `run_training_job(...)`: spawns backend subprocess, parses stdout into normalized `TrainEvent`s, POSTs as metrics.
- `backends/` — pluggable trainer impls behind `TrainerBackend` ABC.
  - `base.py` — `TrainerBackend` abstract class.
  - `__init__.py` — `get_backend(name)` + `resolve_backend_name(...)` registry.
  - `mlx.py` — `MlxBackend` shells out to `mlx_lm.lora` (Apple Silicon).
  - `cuda.py` — `CudaBackend` → `cuda_train.py` (PEFT + TRL + bitsandbytes; Linux+NVIDIA).
- `transfer.py` — adapter/dataset transfer over HTTP (no shared filesystem).

## Important
- Subprocess inherits `os.environ` from worker — `HF_TOKEN` etc. flow through.
- Both `trainer-mlx` and `trainer-cuda` validated by `ensure-trainer-installed` make target (probes `mlx_lm` on mac, `torch+peft+trl` on linux).
- A run queued for a backend with no live worker stays `queued` forever — common cause of "nothing happens."
- JSON logs land in `runs/_trainer.log.json` for Promtail/Loki ingestion.
