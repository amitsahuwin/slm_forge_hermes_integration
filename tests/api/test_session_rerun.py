"""POST /sessions/{sid}/rerun — clone a session's config into a new queued one.

The companion of ADR-0004: the API never auto-resumes user work on boot,
so the user needs an explicit way to rerun a stranded or finished
experiment without re-typing every hyperparameter.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.run import Run, RunStatus
from apps.api.models.session import SessionStatus, TargetMetric, TrainingSession


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rerun.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _rerun(sid, db: Session):
    """Call the underlying ``rerun_session`` function without the
    @requires decorator. Functools.wraps preserves ``__wrapped__``."""
    from apps.api.routers.sessions import rerun_session
    from tests.api._isolation_helpers import synth_admin_request

    return rerun_session.__wrapped__(sid, synth_admin_request(), db)


def _mk_source(db: Session, **kw) -> TrainingSession:
    # Phase D — stamp identity matching the synth admin used by _rerun()
    kw.setdefault("tenant_id", "local")
    kw.setdefault("user_id", "local-admin")
    kw.setdefault("role", "admin")
    sess = TrainingSession(
        name=kw.pop("name", "src-exp"),
        dataset=kw.pop("dataset", "demo"),
        base_model=kw.pop("base_model", "mlx-community/Qwen2.5-3B-Instruct-4bit"),
        trainer_backend=kw.pop("trainer_backend", "mlx"),
        status=kw.pop("status", SessionStatus.COMPLETED),
        **kw,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def test_rerun_clones_config_into_new_queued_session(db_session) -> None:
    src = _mk_source(
        db_session,
        name="exp-a",
        dataset="ds-x",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        trainer_backend="cuda",
        iters=200,
        batch_size=8,
        learning_rate=2e-4,
        num_layers=12,
        max_seq_length=1024,
        max_rounds=5,
        plateau_patience=2,
        min_delta=0.01,
        target_metric=TargetMetric.CANARY_LOSS,
        canary_drift_threshold=0.25,
        status=SessionStatus.COMPLETED,
        best_run_id=42,
        best_metric_value=0.123,
        error_message=None,
    )

    new = _rerun(src.id, db_session)

    assert new.id is not None and new.id != src.id
    assert new.status == SessionStatus.QUEUED
    # Config fields cloned verbatim.
    assert new.dataset == src.dataset
    assert new.base_model == src.base_model
    assert new.trainer_backend == src.trainer_backend
    assert new.iters == src.iters
    assert new.batch_size == src.batch_size
    assert new.learning_rate == src.learning_rate
    assert new.num_layers == src.num_layers
    assert new.max_seq_length == src.max_seq_length
    assert new.max_rounds == src.max_rounds
    assert new.plateau_patience == src.plateau_patience
    assert new.min_delta == src.min_delta
    assert new.target_metric == src.target_metric
    assert new.canary_drift_threshold == src.canary_drift_threshold
    # Result/run-progress fields explicitly reset.
    assert new.best_run_id is None
    assert new.best_metric_value is None
    assert new.error_message is None
    assert new.current_round == 0
    assert new.started_at is None
    assert new.completed_at is None
    # Name is recognizable as a rerun, distinct from the source.
    assert new.name != src.name
    assert src.name in new.name


def test_rerun_does_not_mutate_source(db_session) -> None:
    src = _mk_source(
        db_session,
        status=SessionStatus.FAILED,
        error_message="Server restarted while this experiment was in progress.",
        best_run_id=7,
        best_metric_value=0.5,
    )
    src_id = src.id

    _rerun(src_id, db_session)

    db_session.expire_all()
    src_after = db_session.get(TrainingSession, src_id)
    assert src_after.status == SessionStatus.FAILED  # untouched
    assert src_after.error_message == (
        "Server restarted while this experiment was in progress."
    )
    assert src_after.best_run_id == 7
    assert src_after.best_metric_value == pytest.approx(0.5)


def test_rerun_does_not_clone_child_runs(db_session) -> None:
    """The new session should be queued empty — the ratchet loop will
    create fresh runs when it picks the session up."""
    src = _mk_source(db_session, status=SessionStatus.COMPLETED)
    r1 = Run(
        dataset="demo", base_model="m/x", session_id=src.id,
        status=RunStatus.COMPLETED, final_val_loss=0.4, iteration_number=0,
    )
    r2 = Run(
        dataset="demo", base_model="m/x", session_id=src.id,
        status=RunStatus.COMPLETED, final_val_loss=0.3, iteration_number=1,
    )
    db_session.add(r1)
    db_session.add(r2)
    db_session.commit()

    new = _rerun(src.id, db_session)

    assert new.id is not None
    children = db_session.exec(
        select(Run).where(Run.session_id == new.id)
    ).all()
    assert children == []
    # Source's children are not reparented.
    src_children = db_session.exec(
        select(Run).where(Run.session_id == src.id)
    ).all()
    assert len(src_children) == 2


def test_rerun_unknown_session_returns_404(db_session) -> None:
    with pytest.raises(HTTPException) as exc:
        _rerun(99999, db_session)
    assert exc.value.status_code == 404


def test_rerun_persists_new_session(db_session) -> None:
    """The new session must be committed and queryable."""
    src = _mk_source(db_session, name="persist-src",
                     status=SessionStatus.COMPLETED)

    new = _rerun(src.id, db_session)

    db_session.expire_all()
    fetched = db_session.get(TrainingSession, new.id)
    assert fetched is not None
    assert fetched.status == SessionStatus.QUEUED
    assert fetched.name != src.name