"""Phase D follow-up — when a run reaches a terminal status, its local
artifact dir is swept to the configured object store.

These tests pin the contract:

* ``sync_local_dir_to_store`` is a no-op when ``SLM_FORGE_STORAGE=local``.
* It calls ``store.put`` once per file (recursive walk).
* The bucket is ensured before any put.
* ``patch_run`` queues the sync as a background task on COMPLETED.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.api.services.identity import Identity
from apps.api.services.storage import uploader as uploader_module


def _identity() -> Identity:
    return Identity(
        tenant_id="acme",
        user_id="alice@acme",
        role="data_engineer",
        email=None,
        scopes=frozenset(),
        is_admin=False,
        is_worker=False,
    )


@pytest.mark.asyncio
async def test_sync_no_op_when_storage_local(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_STORAGE", "local")
    (tmp_path / "adapter.bin").write_bytes(b"x" * 16)

    # If sync_local_dir_to_store tried to talk to S3 it would error
    # because no Ozone is configured here. The local-mode short-circuit
    # is the contract.
    counters = await uploader_module.sync_local_dir_to_store(
        tmp_path, identity=_identity(), kind="runs", artifact_id=1
    )
    assert counters == {"files": 0, "bytes": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_sync_puts_every_file_under_tenant_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_STORAGE", "s3")

    # Seed two files in a nested layout — sync walks recursively.
    (tmp_path / "adapter").mkdir()
    (tmp_path / "adapter" / "weights.bin").write_bytes(b"abc")
    (tmp_path / "metadata.json").write_text('{"k": 1}')

    fake_store = MagicMock()
    fake_store.put = AsyncMock()

    monkeypatch.setattr(uploader_module, "get_object_store", lambda _id: fake_store)
    monkeypatch.setattr(uploader_module, "ensure_tenant_bucket", AsyncMock())

    counters = await uploader_module.sync_local_dir_to_store(
        tmp_path, identity=_identity(), kind="runs", artifact_id=42
    )

    assert counters["files"] == 2
    assert counters["bytes"] == 3 + len('{"k": 1}')
    # Both files were put under the canonical tenant key.
    put_keys = [call.args[0] for call in fake_store.put.await_args_list]
    assert "acme/data_engineer/alice@acme/runs/42/adapter/weights.bin" in put_keys
    assert "acme/data_engineer/alice@acme/runs/42/metadata.json" in put_keys


@pytest.mark.asyncio
async def test_sync_skips_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SLM_FORGE_STORAGE", "s3")
    monkeypatch.setattr(uploader_module, "ensure_tenant_bucket", AsyncMock())
    monkeypatch.setattr(uploader_module, "get_object_store", lambda _id: MagicMock())

    counters = await uploader_module.sync_local_dir_to_store(
        tmp_path / "nonexistent",
        identity=_identity(),
        kind="runs",
        artifact_id=42,
    )
    assert counters == {"files": 0, "bytes": 0, "skipped": 0}


def test_patch_run_queues_sync_on_completed(monkeypatch):
    """Confirm ``patch_run`` queues ``sync_local_dir_to_store`` as a
    background task when a run transitions to COMPLETED."""
    import asyncio

    from sqlmodel import Session, SQLModel, create_engine

    from apps.api.models.run import Run, RunMethod, RunStatus
    from apps.api.models.session import TrainingSession
    from apps.api.routers import runs as runs_router

    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        eng,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )

    with Session(eng) as s:
        run = Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.RUNNING,
            tenant_id="acme", user_id="alice@acme", role="data_engineer",
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    # Capture the background task.
    scheduled: list[tuple] = []

    class _Bg:
        def add_task(self, fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))

    from tests.api._isolation_helpers import make_request
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")

    with Session(eng) as s:
        runs_router.patch_run(
            run_id=run_id,
            payload=runs_router.RunPatch(status=RunStatus.COMPLETED),
            request=req,
            session=s,
            background=_Bg(),  # type: ignore[arg-type]
        )

    # One scheduled task: sync_local_dir_to_store
    sync_calls = [
        c for c in scheduled
        if getattr(c[0], "__name__", "") == "sync_local_dir_to_store"
    ]
    assert sync_calls, f"sync_local_dir_to_store not queued — saw {scheduled}"
    _, args, kwargs = sync_calls[0]
    assert kwargs["kind"] == "runs"
    assert kwargs["artifact_id"] == run_id
    assert kwargs["identity"].tenant_id == "acme"
    assert kwargs["identity"].user_id == "alice@acme"
    # Quiet "coroutine never awaited" warning from the (deliberately
    # un-awaited) test.
    del asyncio