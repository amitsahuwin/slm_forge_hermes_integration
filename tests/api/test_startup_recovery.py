"""Startup recovery — never auto-resume user work on API boot.

Bug being fixed: ``_recover_stranded_runs_and_sessions`` unconditionally
flipped every TrainingSession.status == RUNNING back to QUEUED on every
API restart, which made the ratchet worker pick the session up again and
hammer Ollama with fresh mutation proposals. The new rule is reconcile-
or-fail: API startup transitions stranded work to a terminal state and
the user reruns explicitly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.main import _recover_stranded
from apps.api.models.run import Run, RunStatus
from apps.api.models.session import SessionStatus, TrainingSession
from apps.api.services import claims


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _mk_session(db: Session, **kw) -> TrainingSession:
    name = kw.pop("name", "exp")
    status = kw.pop("status", SessionStatus.RUNNING)
    sess = TrainingSession(
        name=name, dataset="demo", base_model="m/x", status=status, **kw,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def _mk_run(
    db: Session,
    *,
    session_id: int | None = None,
    status: RunStatus = RunStatus.QUEUED,
    **kw,
) -> Run:
    run = Run(
        dataset="demo",
        base_model="m/x",
        session_id=session_id,
        status=status,
        **kw,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Session-level: reconcile-or-fail
# ---------------------------------------------------------------------------


def test_running_session_with_completed_child_reconciles_to_completed(
    db: Session,
) -> None:
    """Common bug case: ratchet finished its rounds but the final
    ``patch_session(status='completed')`` PATCH never landed (server
    restart between the last run finishing and the status flip). All
    child runs are COMPLETED. Recovery must NOT re-queue — it must
    reconcile to COMPLETED so the user keeps the result."""
    sess = _mk_session(db, status=SessionStatus.RUNNING)
    _mk_run(db, session_id=sess.id, status=RunStatus.COMPLETED, final_val_loss=0.42)
    _mk_run(db, session_id=sess.id, status=RunStatus.COMPLETED, final_val_loss=0.38)

    _recover_stranded(db)

    db.refresh(sess)
    assert sess.status == SessionStatus.COMPLETED
    assert sess.best_run_id is not None
    assert sess.best_metric_value == pytest.approx(0.38)
    assert (sess.error_message or "") == ""


def test_running_session_with_all_children_failed_fails_with_rerun_hint(
    db: Session,
) -> None:
    sess = _mk_session(db, status=SessionStatus.RUNNING)
    _mk_run(db, session_id=sess.id, status=RunStatus.FAILED)
    _mk_run(db, session_id=sess.id, status=RunStatus.FAILED)

    _recover_stranded(db)

    db.refresh(sess)
    assert sess.status == SessionStatus.FAILED
    assert "rerun" in (sess.error_message or "").lower()


def test_running_session_with_legacy_running_child_fails_both(db: Session) -> None:
    """Legacy RUNNING child run with no claim record. Old code re-queued
    it (auto-resume). New rule: the run is marked FAILED and the parent
    session goes to FAILED with a 'rerun manually' message."""
    sess = _mk_session(db, status=SessionStatus.RUNNING)
    orphan = _mk_run(db, session_id=sess.id, status=RunStatus.RUNNING)
    # claimed_at left None — pre-Phase-R legacy row.

    _recover_stranded(db)

    db.refresh(sess)
    db.refresh(orphan)
    assert orphan.status == RunStatus.FAILED   # NOT QUEUED
    assert orphan.claimed_by is None
    assert orphan.claimed_at is None
    assert sess.status == SessionStatus.FAILED
    assert "rerun" in (sess.error_message or "").lower()


def test_running_session_with_no_children_fails(db: Session) -> None:
    """Session marked RUNNING but no runs ever got created (ratchet
    crashed immediately after the status=running PATCH). No work to
    preserve — fail it with a clear rerun message."""
    sess = _mk_session(db, status=SessionStatus.RUNNING)

    _recover_stranded(db)

    db.refresh(sess)
    assert sess.status == SessionStatus.FAILED
    assert "rerun" in (sess.error_message or "").lower()


def test_already_terminal_session_is_untouched(db: Session) -> None:
    sess = _mk_session(
        db,
        status=SessionStatus.COMPLETED,
        best_run_id=99,
        best_metric_value=0.1,
        error_message=None,
    )

    _recover_stranded(db)

    db.refresh(sess)
    assert sess.status == SessionStatus.COMPLETED
    assert sess.best_run_id == 99
    assert sess.best_metric_value == pytest.approx(0.1)
    assert sess.error_message is None


def test_recovery_never_writes_queued_session(db: Session) -> None:
    """Belt-and-braces: across every plausible starting shape, no
    session is ever flipped back to QUEUED on startup. That re-queue is
    what was waking the ratchet worker and hammering Ollama on every
    container restart."""
    a = _mk_session(db, name="a", status=SessionStatus.RUNNING)
    _mk_run(db, session_id=a.id, status=RunStatus.COMPLETED, final_val_loss=0.5)
    b = _mk_session(db, name="b", status=SessionStatus.RUNNING)
    _mk_run(db, session_id=b.id, status=RunStatus.FAILED)
    _mk_session(db, name="c", status=SessionStatus.RUNNING)  # no children

    _recover_stranded(db)

    sessions = db.exec(select(TrainingSession)).all()
    assert all(s.status != SessionStatus.QUEUED for s in sessions)


def test_existing_best_run_id_is_preserved(db: Session) -> None:
    """If the ratchet already wrote best_run_id mid-experiment, the
    reconcile path must not clobber it with a re-derived guess."""
    sess = _mk_session(db, status=SessionStatus.RUNNING, best_run_id=None,
                       best_metric_value=None)
    winner = _mk_run(
        db, session_id=sess.id, status=RunStatus.COMPLETED, final_val_loss=0.6,
    )
    sess.best_run_id = winner.id
    sess.best_metric_value = 0.6
    db.add(sess)
    db.commit()
    _mk_run(
        db, session_id=sess.id, status=RunStatus.COMPLETED, final_val_loss=0.3,
    )

    _recover_stranded(db)

    db.refresh(sess)
    assert sess.status == SessionStatus.COMPLETED
    assert sess.best_run_id == winner.id
    assert sess.best_metric_value == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Run-level: stranded_action='fail' for startup recovery
# ---------------------------------------------------------------------------


def test_startup_recovery_marks_legacy_runs_failed_not_queued(db: Session) -> None:
    legacy = _mk_run(db, status=RunStatus.RUNNING)  # no session_id, no claim

    released = claims.release_expired_claims(
        db, include_legacy=True, stranded_action="fail",
    )

    assert released == 1
    db.refresh(legacy)
    assert legacy.status == RunStatus.FAILED
    assert legacy.claimed_by is None
    assert legacy.claimed_at is None
    assert (legacy.error_message or "")


def test_startup_recovery_marks_expired_claimed_runs_failed(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(claims.CLAIM_TIMEOUT_ENV, "60")
    run = _mk_run(db)
    claims.claim_next_run(db, backend="mlx", worker_id="dead-worker")
    # Age the claim past the timeout.
    db_run = db.get(Run, run.id)
    db_run.claimed_at = datetime.now(UTC) - timedelta(minutes=120)
    db.add(db_run)
    db.commit()

    released = claims.release_expired_claims(
        db, include_legacy=True, stranded_action="fail",
    )

    assert released == 1
    db.refresh(run)
    assert run.status == RunStatus.FAILED
    assert run.claimed_by is None
    assert run.claimed_at is None


def test_default_release_still_requeues_for_living_pool(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-operation lease sweep (called from claim_next_run with the
    default stranded_action='requeue') must keep its existing behavior:
    a stale claim is re-queued so a living worker can take over."""
    monkeypatch.setenv(claims.CLAIM_TIMEOUT_ENV, "60")
    stale = _mk_run(db)
    claims.claim_next_run(db, backend="mlx", worker_id="dead-worker")
    db_stale = db.get(Run, stale.id)
    db_stale.claimed_at = datetime.now(UTC) - timedelta(minutes=120)
    db.add(db_stale)
    db.commit()

    got = claims.claim_next_run(db, backend="mlx", worker_id="alive")

    assert got is not None
    assert got.id == stale.id
    assert got.status == RunStatus.RUNNING
    assert got.claimed_by == "alive"


def test_legacy_test_default_still_requeues(db: Session) -> None:
    """Pin the existing default-behavior contract from
    ``test_release_expired_claims_requeues_legacy_rows`` — must still
    pass with the new default ``stranded_action='requeue'``."""
    legacy = _mk_run(db)
    legacy.status = RunStatus.RUNNING
    db.add(legacy)
    db.commit()

    released = claims.release_expired_claims(db, include_legacy=True)

    assert released == 1
    db.refresh(legacy)
    assert legacy.status == RunStatus.QUEUED