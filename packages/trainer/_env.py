"""Load the project ``.env`` for the trainer worker.

The CUDA training subprocess inherits ``os.environ`` from the worker
(``packages/trainer/runner.py``), so gated Hugging Face downloads need
``HF_TOKEN`` present in the worker's environment. Unlike the API (run via
docker-compose ``env_file``) or ``ratchet/hermes_bridge``, the host trainer
worker is launched with a bare ``uv run`` and would otherwise never see
``.env``. Mirrors the guarded ``load_dotenv`` pattern used elsewhere.
"""
from __future__ import annotations

from pathlib import Path

# packages/trainer/_env.py → repo root is two levels up from the package dir.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_project_env(path: Path | None = None) -> bool:
    """Load ``.env`` (default: project root) into ``os.environ``.

    ``override=False`` so an explicitly-exported variable always wins over the
    file. Returns whether python-dotenv loaded a file (False if dotenv is
    missing or the file does not exist); never raises.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    env_path = path or PROJECT_ROOT / ".env"
    return load_dotenv(env_path, override=False)
