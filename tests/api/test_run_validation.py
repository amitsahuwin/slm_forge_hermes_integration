"""Phase P / A4, A5 — create_run enforces the catalog; defaults are fixed.

PR-3 — ``create_run`` became ``async`` and its 422 detail is now a
``{"message": str, "remedy": str | None}`` dict. The tests below have been
updated to ``await`` it and to read ``detail["message"]``. The remedy
*content* itself is asserted in ``test_remedy_translation.py``.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.run import Run
from apps.api.models.session import TrainingSession  # FK target for Run.session_id
from apps.api.routers.runs import RunCreate, create_run
from apps.api.services import model_catalog as mc
from apps.api.services import remedy as remedy_module


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _stub_remedy(monkeypatch: pytest.MonkeyPatch):
    """Keep these tests focused on catalog validation, not Hermes plumbing."""
    monkeypatch.setattr(remedy_module, "_invoke_skill", lambda payload: "")
    remedy_module.clear_cache()
    yield
    remedy_module.clear_cache()


@pytest.mark.asyncio
async def test_valid_run_is_persisted(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    payload = RunCreate(
        dataset="demo",
        base_model="mlx-community/gemma-3-4b-it-4bit",
        trainer_backend="mlx",
    )
    run = await create_run(payload, db_session)
    assert run.id is not None
    assert run.trainer_backend == "mlx"


@pytest.mark.asyncio
async def test_uncataloged_model_is_422_and_not_persisted(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    payload = RunCreate(dataset="demo", base_model="totally/made-up")
    with pytest.raises(HTTPException) as exc:
        await create_run(payload, db_session)
    assert exc.value.status_code == 422
    # PR-3 contract: detail is dict-shaped with a stable ``message`` field.
    assert isinstance(exc.value.detail, dict)
    assert "message" in exc.value.detail
    assert db_session.exec(select(Run)).first() is None


@pytest.mark.asyncio
async def test_broken_model_is_422(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    payload = RunCreate(dataset="demo", base_model="mlx-community/gemma-3n-E2B-it-bf16")
    with pytest.raises(HTTPException) as exc:
        await create_run(payload, db_session)
    assert exc.value.status_code == 422
    assert isinstance(exc.value.detail, dict)


@pytest.mark.asyncio
async def test_escape_hatch_allows_uncataloged(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLM_FORGE_ENFORCE_CATALOG", "false")
    payload = RunCreate(dataset="demo", base_model="totally/made-up")
    run = await create_run(payload, db_session)
    assert run.id is not None


# ---------------------------------------------------------------------------
# A5 — defaults no longer point at the broken gemma-3n checkpoint
# ---------------------------------------------------------------------------

def test_run_create_default_is_catalog_default() -> None:
    assert RunCreate(dataset="demo").base_model == mc.default_model_id("mlx")


def test_catalog_default_is_stable() -> None:
    model = mc.get_model_by_key(mc.DEFAULT_MODEL_KEY)
    assert model is not None
    assert model.backends["mlx"].status == "stable"
