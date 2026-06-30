"""Phase D — clean-slate cutover script.

Truncates every SQLModel-mapped table, removes runtime artifact roots,
deletes and recreates every ``slm-forge-*`` Ozone bucket, then re-seeds
the bundled sample datasets under ``data/datasets/global/``.

Refuses to run unless ``SLM_FORGE_WIPE_CONFIRM=YES`` is set in the
environment — *this script destroys data*. Every destructive action is
logged as a JSON line so an operator can audit the cutover.

Usage:
    SLM_FORGE_WIPE_CONFIRM=YES uv run python scripts/wipe_clean.py

The ``make wipe-clean`` Makefile target is the recommended entry point;
it sets the right env vars and runs from the repo root.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# Hardcoded table list — see docs/specs/2026-06-30-phase-d-user-isolation.md §6.
# Explicit, not derived from SQLModel metadata, to avoid accidental drops.
TABLES: tuple[str, ...] = (
    "runs",
    "sessions",
    "exports",
    "metrics",
    "hermes_traces",
    "auto_fix_attempt",
    "chat_conversations",
    "chat_messages",
    "heartbeat",
)

# Filesystem roots wiped during cutover. Configurable via env so the
# script can also clean a dev machine where paths differ from /app.
DEFAULT_ARTIFACT_ROOTS: tuple[str, ...] = (
    "/app/runs",
    "/app/exports",
    "/app/storage",
)
DEFAULT_USER_DATASETS_ROOT = "data/datasets/users"
DEFAULT_BUCKET_PREFIX = "slm-forge-"


def _log(event: str, **kw: Any) -> None:
    """Structured single-line JSON event log."""
    print(json.dumps({"ts": time.time(), "event": event, **kw}), flush=True)


def _refuse_unconfirmed() -> None:
    if os.environ.get("SLM_FORGE_WIPE_CONFIRM") != "YES":
        sys.stderr.write(
            "Refusing to wipe — set SLM_FORGE_WIPE_CONFIRM=YES to confirm.\n"
            "This script destroys ALL runs, experiments, exports, metrics,\n"
            "traces, chats, artifact directories, and Ozone buckets.\n"
        )
        sys.exit(2)


def truncate_tables() -> None:
    """Delete every row from each table. The script is idempotent: a
    second run is a no-op for tables that are already empty."""
    from sqlmodel import Session, text

    from apps.api.services.db import engine

    with Session(engine) as s:
        for tbl in TABLES:
            try:
                result = s.exec(text(f"DELETE FROM {tbl}"))  # type: ignore[call-arg]
                rowcount = getattr(result, "rowcount", None)
                _log("truncated", table=tbl, rows_deleted=rowcount)
            except Exception as e:  # pragma: no cover
                # Table may not exist yet (fresh install). Log and move on.
                _log("truncate_skipped", table=tbl, reason=str(e))
        s.commit()


def remove_artifact_dirs() -> None:
    """Wipe per-user / per-tenant filesystem state but preserve the
    ``global/`` bundled samples (re-seeded after schema recreation)."""
    roots = os.environ.get("SLM_FORGE_WIPE_ARTIFACT_ROOTS")
    targets = roots.split(":") if roots else list(DEFAULT_ARTIFACT_ROOTS)
    # Phase D.3 — per-user dirs across every file-based router.
    targets.extend([
        DEFAULT_USER_DATASETS_ROOT,            # data/datasets/users/
        "data/research/users",                  # research reports
        "data/.ingest_staging/users",           # ingest staging
    ])
    for t in targets:
        p = Path(t)
        if not p.exists():
            _log("skip_missing_dir", path=str(p))
            continue
        if not p.is_dir():
            _log("skip_non_dir", path=str(p))
            continue
        shutil.rmtree(p)
        _log("removed_dir", path=str(p))


def reset_ozone_buckets() -> None:
    """List every bucket matching ``slm-forge-*`` and recreate it empty.
    If Ozone isn't configured, this is a no-op."""
    prefix = os.environ.get("SLM_FORGE_OZONE_BUCKET_PREFIX", DEFAULT_BUCKET_PREFIX)
    try:
        from apps.api.services.storage.factory import get_object_store
    except Exception as e:  # pragma: no cover
        _log("ozone_unavailable", reason=str(e))
        return

    # Storage factory binds per-identity; for the wipe we need a raw
    # admin store. We attempt to obtain one via the factory's
    # "system" identity helper if it exists, otherwise we skip.
    try:
        store = get_object_store()  # type: ignore[call-arg]
        # Local backends don't have buckets per se; skip.
        if not hasattr(store, "list_buckets"):
            _log("ozone_skip_non_ozone_backend")
            return
        buckets = store.list_buckets()  # type: ignore[attr-defined]
    except Exception as e:
        _log("ozone_list_failed", reason=str(e))
        return

    for b in buckets:
        if not b.startswith(prefix):
            _log("ozone_skip_unsafe_prefix", bucket=b)
            continue
        try:
            store.delete_bucket(b)  # type: ignore[attr-defined]
            store.create_bucket(b)  # type: ignore[attr-defined]
            _log("ozone_recreated_bucket", bucket=b)
        except Exception as e:
            _log("ozone_bucket_op_failed", bucket=b, reason=str(e))


def recreate_schema() -> None:
    from apps.api.services.db import init_db

    init_db()
    _log("schema_recreated")


def reseed_global_datasets() -> None:
    """Re-run seed_datasets.py so bundled samples are restored under
    ``data/datasets/global/``. Skips silently if the seed script is
    missing — operators on minimal installs may have removed it."""
    import subprocess
    seed = Path(__file__).parent / "seed_datasets.py"
    if not seed.exists():
        _log("seed_skipped", reason="scripts/seed_datasets.py not found")
        return
    proc = subprocess.run(
        [sys.executable, str(seed)],
        capture_output=True,
        text=True,
    )
    _log(
        "reseeded",
        returncode=proc.returncode,
        stdout_lines=len(proc.stdout.splitlines()),
        stderr_lines=len(proc.stderr.splitlines()),
    )


def main() -> int:
    _refuse_unconfirmed()
    _log("wipe_started")
    truncate_tables()
    remove_artifact_dirs()
    reset_ozone_buckets()
    recreate_schema()
    reseed_global_datasets()
    _log("wipe_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())