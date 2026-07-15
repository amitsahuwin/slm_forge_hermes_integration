"""Models router — dynamic registry endpoints + catalog views.

Covers ``POST /download`` (202 + queued job, validation, backend override),
``GET /registry``, ``DELETE /registry/{key}``, admin-only enforcement, and the
dynamic catalog views (``/`` legacy skips cuda-only; ``/v2`` includes registered
models). The background scheduler is stubbed so no network/HF call happens.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.middleware import auth as auth_module
from apps.api.middleware.auth import User
from apps.api.models.model_download_job import ModelDownloadJob, ModelDownloadStatus
from apps.api.models.registered_model import RegisteredModel
from apps.api.routers import models as models_router
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture()
def engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    auth_settings_module.get_auth_settings.cache_clear()
    # Auth disabled by default → @requires is a pass-through for happy paths.
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (True, ""),
    )
    # Never hit HF / spawn a task in router tests.
    scheduled: list[int] = []
    monkeypatch.setattr(models_router, "_schedule_download", scheduled.append)
    yield eng, scheduled
    eng.dispose()
    auth_settings_module.get_auth_settings.cache_clear()


def _req(user: User | None = None) -> MagicMock:
    req = MagicMock()
    req.state.user = user or User(
        id="alice", email="alice@x", roles=["admin"], groups=["/tenants/acme"]
    )
    return req


def _register(eng, **over: object) -> RegisteredModel:
    row = RegisteredModel(
        key="qwen2.5-1.5b-instruct",
        label="Qwen 2.5 1.5B Instruct",
        family="qwen",
        size_params="1.5B",
        backend="cuda",
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        min_memory_gb=6,
        quant="nf4",
        status="untested",
        gated=False,
        notes="registered via test",
        created_by_user_id="alice",
        created_by_tenant_id="acme",
    )
    for k, v in over.items():
        setattr(row, k, v)
    with Session(eng) as s:
        s.add(row)
        s.commit()
    return row


# --------------------------------------------------------------------------- #
# POST /download
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_download_queues_job(engine) -> None:
    eng, scheduled = engine
    resp = await models_router.download_model(
        request=_req(),
        body=models_router.DownloadRequest(hf_id="Qwen/Qwen2.5-1.5B-Instruct"),
    )
    assert resp.status == "queued"
    assert resp.target_backend == "cuda"  # auto-detected
    assert resp.job_id.startswith("modeldownload:")

    with Session(eng) as s:
        row = s.exec(select(ModelDownloadJob)).first()
        assert row is not None
        assert row.status == ModelDownloadStatus.QUEUED
        assert row.hf_id == "Qwen/Qwen2.5-1.5B-Instruct"
        assert row.tenant_id == "acme"
        assert scheduled == [row.id]


@pytest.mark.asyncio
async def test_download_backend_override(engine) -> None:
    # mlx-community id would auto-detect mlx, but the caller forces cuda.
    resp = await models_router.download_model(
        request=_req(),
        body=models_router.DownloadRequest(
            hf_id="mlx-community/Qwen2.5-3B-Instruct-4bit", backend="cuda"
        ),
    )
    assert resp.target_backend == "cuda"


@pytest.mark.asyncio
async def test_download_rejects_bad_hf_id(engine) -> None:
    with pytest.raises(HTTPException) as ei:
        await models_router.download_model(
            request=_req(),
            body=models_router.DownloadRequest(hf_id="not a valid id!!"),
        )
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_download_rejects_bad_backend(engine) -> None:
    with pytest.raises(HTTPException) as ei:
        await models_router.download_model(
            request=_req(),
            body=models_router.DownloadRequest(
                hf_id="Qwen/Qwen2.5-1.5B-Instruct", backend="tpu"
            ),
        )
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_download_admin_only_enforced(engine, monkeypatch) -> None:
    # Enable auth and deny at the policy layer → 403 before any DB write.
    monkeypatch.setattr(
        auth_module,
        "get_auth_settings",
        lambda: SimpleNamespace(auth_enabled=True),
    )
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (False, "denied: create on model"),
    )
    viewer = User(id="v1", email="v@x", roles=["viewer"], groups=["/tenants/acme"])
    with pytest.raises(HTTPException) as ei:
        await models_router.download_model(
            request=_req(viewer),
            body=models_router.DownloadRequest(hf_id="Qwen/Qwen2.5-1.5B-Instruct"),
        )
    assert ei.value.status_code == 403


# --------------------------------------------------------------------------- #
# GET /registry + DELETE /registry/{key}
# --------------------------------------------------------------------------- #
def test_list_registry_returns_registered(engine) -> None:
    eng, _ = engine
    _register(eng)
    entries = models_router.list_registry()
    assert len(entries) == 1
    assert entries[0].model_id == "Qwen/Qwen2.5-1.5B-Instruct"
    assert entries[0].backend == "cuda"


@pytest.mark.asyncio
async def test_delete_registered_removes_row(engine) -> None:
    eng, _ = engine
    _register(eng)
    await models_router.delete_registered(request=_req(), key="qwen2.5-1.5b-instruct")
    with Session(eng) as s:
        assert s.exec(select(RegisteredModel)).first() is None


@pytest.mark.asyncio
async def test_delete_registered_missing_is_404(engine) -> None:
    with pytest.raises(HTTPException) as ei:
        await models_router.delete_registered(request=_req(), key="nope")
    assert ei.value.status_code == 404


# --------------------------------------------------------------------------- #
# Dynamic catalog views
# --------------------------------------------------------------------------- #
def test_v2_includes_registered_model(engine) -> None:
    eng, _ = engine
    _register(eng)
    keys = {m.key for m in models_router.list_models_v2()}
    assert "qwen2.5-1.5b-instruct" in keys


def test_legacy_view_skips_cuda_only_registered(engine) -> None:
    eng, _ = engine
    _register(eng)  # cuda-only registered model
    ids = {b.hf_id for b in models_router.list_models()}
    # The cuda-only registered checkpoint has no mlx variant → skipped.
    assert "Qwen/Qwen2.5-1.5B-Instruct" not in ids
    # Seeds (all have mlx) are still present.
    assert any(i.startswith("mlx-community/") for i in ids)