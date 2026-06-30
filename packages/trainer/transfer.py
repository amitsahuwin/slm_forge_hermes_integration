"""Worker-side dataset download + adapter upload (Phase R, remote mode).

Used when ``SLM_FORGE_REMOTE_WORKER=true`` — the worker has no shared
filesystem with the API host, so datasets arrive as tar.gz archives from
``GET /datasets/{name}/archive`` and adapters return via
``POST /runs/{id}/artifacts``.

See ``docs/specs/PHASE_R_SPEC.md`` §3.5.
"""
from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

import httpx

log = logging.getLogger("trainer.transfer")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data" / "datasets"

REMOTE_ENV = "SLM_FORGE_REMOTE_WORKER"
_TRUTHY = {"1", "true", "yes", "on"}


class TransferError(RuntimeError):
    """Raised when a dataset archive is unsafe or cannot be retrieved."""


def remote_mode() -> bool:
    return os.environ.get(REMOTE_ENV, "").strip().lower() in _TRUTHY


def _validate_members(members: list[tarfile.TarInfo]) -> None:
    for m in members:
        parts = Path(m.name).parts
        if m.name.startswith("/") or ".." in parts or m.issym() or m.islnk() or m.isdev():
            raise TransferError(f"Unsafe archive member: {m.name!r}")


def ensure_dataset_local(dataset: str, api_url: str) -> Path:
    """Return the local dataset dir, downloading the archive if missing.

    Phase D.3 — checks the new ``global/<name>/`` layout first, then the
    legacy flat path. Newly-downloaded archives land under ``global/``
    so the trainer's ``_resolve_dataset_dir`` finds them on next call.
    """
    global_dir = DATA_ROOT / "global" / dataset
    legacy_dir = DATA_ROOT / dataset
    if (global_dir / "train.jsonl").exists():
        return global_dir
    if (legacy_dir / "train.jsonl").exists():
        return legacy_dir

    url = f"{api_url}/api/v1/datasets/{dataset}/archive"
    log.info("Dataset '%s' missing locally — downloading %s", dataset, url)
    try:
        r = httpx.get(url, timeout=120)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise TransferError(f"Dataset archive download failed: {e}") from e

    extract_root = DATA_ROOT / "global"
    try:
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
            members = tf.getmembers()
            _validate_members(members)
            extract_root.mkdir(parents=True, exist_ok=True)
            tf.extractall(extract_root, members=members)
    except tarfile.TarError as e:
        raise TransferError(f"Invalid dataset archive: {e}") from e

    if not (global_dir / "train.jsonl").exists():
        raise TransferError(
            f"Archive for '{dataset}' extracted but train.jsonl is still missing"
        )
    log.info("Dataset '%s' ready at %s", dataset, global_dir)
    return global_dir


def upload_adapter(run_id: int, adapter_dir: Path, api_url: str) -> bool:
    """Tar the adapter dir and POST it to the API. Never raises.

    Returns False on any failure — the run still completes; the operator
    sees a warning and can re-upload manually.
    """
    try:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(adapter_dir, arcname="adapter")
        r = httpx.post(
            f"{api_url}/api/v1/runs/{run_id}/artifacts",
            files={"archive": ("adapter.tar.gz", buf.getvalue(), "application/gzip")},
            timeout=600,
        )
        r.raise_for_status()
        log.info("Run #%s: adapter uploaded (%d bytes)", run_id, buf.getbuffer().nbytes)
        return True
    except Exception as e:
        log.warning("Run #%s: adapter upload failed: %s", run_id, e)
        return False
