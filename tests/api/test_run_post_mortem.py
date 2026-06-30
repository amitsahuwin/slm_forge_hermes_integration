"""PR-2 — HTTP wiring: ``PATCH /runs/{id}`` enqueues + ``GET /post_mortem`` reads.

These tests exercise the FastAPI route handlers directly (no TestClient
needed) — we focus on the BackgroundTasks integration and the response
schema. The skill invocation itself is covered by
``test_post_mortem_service.py``.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.run import Run, RunStatus
from apps.api.models.session import TrainingSession  # FK target for Run.session_id
from apps.api.routers.runs import RunPatch, get_post_mortem, patch_run


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )
    with Session(eng) as s:
        yield s
    eng.dispose()


def _seed(db: Session, status: RunStatus = RunStatus.RUNNING, **overrides: Any) -> Run:
    # Phase D — stamp identity so the synth-admin-driven router can see the row.
    overrides.setdefault("tenant_id", "local")
    overrides.setdefault("user_id", "local-admin")
    overrides.setdefault("role", "admin")
    run = Run(dataset="d", base_model="m", status=status, **overrides)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_patch_to_failed_enqueues_post_mortem(db: Session):
    """First transition into FAILED → BackgroundTasks.add_task called once."""
    run = _seed(db, status=RunStatus.RUNNING)
    assert run.id is not None
    bg = BackgroundTasks()

    payload = RunPatch(status=RunStatus.FAILED, error_message="boom")
    from tests.api._isolation_helpers import synth_admin_request
    patch_run(run.id, payload, synth_admin_request(), db, bg)

    # FastAPI BackgroundTasks stores added tasks on .tasks.
    assert len(bg.tasks) == 1
    task = bg.tasks[0]
    # The task is the ``generate_for_run`` coroutine factory + (run_id,) args.
    assert task.args == (run.id,)
    assert task.func.__name__ == "generate_for_run"


def test_patch_failed_to_failed_does_not_double_enqueue(db: Session):
    """Re-PATCHing a FAILED run with the same status must NOT enqueue twice."""
    run = _seed(db, status=RunStatus.FAILED)
    assert run.id is not None
    bg = BackgroundTasks()

    from tests.api._isolation_helpers import synth_admin_request
    patch_run(run.id, RunPatch(status=RunStatus.FAILED), synth_admin_request(), db, bg)

    assert len(bg.tasks) == 0, (
        "no transition occurred — second BG task would just collide with the cache"
    )


def test_patch_to_completed_does_not_enqueue(db: Session):
    run = _seed(db, status=RunStatus.RUNNING)
    assert run.id is not None
    bg = BackgroundTasks()

    from tests.api._isolation_helpers import synth_admin_request
    patch_run(run.id, RunPatch(status=RunStatus.COMPLETED), synth_admin_request(), db, bg)

    assert len(bg.tasks) == 0


def test_patch_without_status_change_does_not_enqueue(db: Session):
    """A metric-only PATCH (no status change) must not enqueue."""
    run = _seed(db, status=RunStatus.RUNNING)
    assert run.id is not None
    bg = BackgroundTasks()

    from tests.api._isolation_helpers import synth_admin_request
    patch_run(run.id, RunPatch(final_val_loss=0.42), synth_admin_request(), db, bg)

    assert len(bg.tasks) == 0


def test_get_post_mortem_returns_pending_initially(db: Session):
    """After enqueue but before completion, the endpoint reports skipped/pending."""
    run = _seed(db, status=RunStatus.FAILED)
    assert run.id is not None
    from tests.api._isolation_helpers import synth_admin_request
    out = get_post_mortem(run.id, synth_admin_request(), db)
    # Default for a fresh Run is ``"skipped"`` — the background task moves
    # it to ``pending`` then ``ready``/``unavailable``.
    assert out.status == "skipped"
    assert out.markdown is None
    assert out.generated_at is None


def test_get_post_mortem_returns_ready_with_markdown(db: Session):
    """When the service has stored markdown, the endpoint surfaces it."""
    from datetime import UTC, datetime

    run = _seed(db, status=RunStatus.FAILED)
    assert run.id is not None
    run.post_mortem = "# Diagnosis\n\nIt was OOM."
    run.post_mortem_status = "ready"
    run.post_mortem_generated_at = datetime.now(UTC)
    db.add(run)
    db.commit()
    db.refresh(run)

    from tests.api._isolation_helpers import synth_admin_request
    out = get_post_mortem(run.id, synth_admin_request(), db)
    assert out.status == "ready"
    assert out.markdown == "# Diagnosis\n\nIt was OOM."
    assert out.generated_at is not None


def test_get_post_mortem_404_on_unknown_run(db: Session):
    import fastapi

    with pytest.raises(fastapi.HTTPException) as ei:
        from tests.api._isolation_helpers import synth_admin_request
        get_post_mortem(999_999, synth_admin_request(), db)
    assert ei.value.status_code == 404
