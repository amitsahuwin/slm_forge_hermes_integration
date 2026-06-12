"""Phase R / A1-A5 — atomic backend-aware claiming + lease expiry."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.metric import Metric
from apps.api.models.run import Run, RunStatus
from apps.api.services import claims


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claims.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _mk_run(db: Session, *, backend: str | None = "mlx", **kw) -> Run:
    run = Run(dataset="demo", base_model="m/x", **kw)
    run.trainer_backend = backend  # may be None to simulate pre-Phase-O rows
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# A1 — basic claiming
# ---------------------------------------------------------------------------

def test_claim_returns_oldest_and_marks_running(db: Session) -> None:
    first = _mk_run(db)
    _mk_run(db)

    claimed = claims.claim_next_run(db, backend="mlx", worker_id="mac:1")
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status == RunStatus.RUNNING
    assert claimed.claimed_by == "mac:1"
    assert claimed.claimed_at is not None
    assert claimed.started_at is not None


def test_two_claims_get_two_different_runs(db: Session) -> None:
    a = _mk_run(db)
    b = _mk_run(db)
    c1 = claims.claim_next_run(db, backend="mlx", worker_id="w1")
    c2 = claims.claim_next_run(db, backend="mlx", worker_id="w2")
    assert {c1.id, c2.id} == {a.id, b.id}


def test_claim_empty_queue_returns_none(db: Session) -> None:
    assert claims.claim_next_run(db, backend="mlx", worker_id="w") is None


# ---------------------------------------------------------------------------
# A2 — backend isolation
# ---------------------------------------------------------------------------

def test_backend_isolation(db: Session) -> None:
    cuda_run = _mk_run(db, backend="cuda")
    mlx_run = _mk_run(db, backend="mlx")

    got_cuda = claims.claim_next_run(db, backend="cuda", worker_id="gpu:1")
    assert got_cuda.id == cuda_run.id

    got_mlx = claims.claim_next_run(db, backend="mlx", worker_id="mac:1")
    assert got_mlx.id == mlx_run.id

    # Nothing left for either backend.
    assert claims.claim_next_run(db, backend="cuda", worker_id="gpu:1") is None
    assert claims.claim_next_run(db, backend="mlx", worker_id="mac:1") is None


def test_null_backend_rows_claim_as_mlx(db: Session) -> None:
    legacy = _mk_run(db, backend=None)
    assert claims.claim_next_run(db, backend="cuda", worker_id="gpu:1") is None
    got = claims.claim_next_run(db, backend="mlx", worker_id="mac:1")
    assert got is not None and got.id == legacy.id


# ---------------------------------------------------------------------------
# A3 — CAS skips runs stolen between SELECT and UPDATE
# ---------------------------------------------------------------------------

def test_cas_skips_concurrently_taken_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = _mk_run(db)
    fallback = _mk_run(db)

    real_cas = claims._try_claim
    stolen = {"done": False}

    def stealing_cas(session, run_id, worker_id, now):
        # Simulate another worker winning the race for the first candidate.
        if run_id == victim.id and not stolen["done"]:
            stolen["done"] = True
            run = session.get(Run, victim.id)
            run.status = RunStatus.RUNNING
            run.claimed_by = "rival"
            run.claimed_at = now
            session.add(run)
            session.commit()
        return real_cas(session, run_id, worker_id, now)

    monkeypatch.setattr(claims, "_try_claim", stealing_cas)

    got = claims.claim_next_run(db, backend="mlx", worker_id="me")
    assert got is not None
    assert got.id == fallback.id          # victim was skipped, not double-claimed
    db.refresh(victim)
    assert victim.claimed_by == "rival"


# ---------------------------------------------------------------------------
# A4 — lease expiry
# ---------------------------------------------------------------------------

def _age(db: Session, run: Run, minutes: int) -> None:
    run.claimed_at = datetime.now(UTC) - timedelta(minutes=minutes)
    db.add(run)
    db.commit()


def test_stale_claim_is_released_on_next_claim(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claims.CLAIM_TIMEOUT_ENV, "60")
    stale = _mk_run(db)
    claims.claim_next_run(db, backend="mlx", worker_id="dead-worker")
    _age(db, stale, minutes=120)

    got = claims.claim_next_run(db, backend="mlx", worker_id="alive")
    assert got is not None
    assert got.id == stale.id
    assert got.claimed_by == "alive"
    assert "lease" in (got.error_message or "").lower()


def test_recent_metric_keeps_claim_alive(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claims.CLAIM_TIMEOUT_ENV, "60")
    active = _mk_run(db)
    claims.claim_next_run(db, backend="mlx", worker_id="remote-gpu")
    _age(db, active, minutes=999)  # ancient claim timestamp…
    db.add(Metric(run_id=active.id, step=50, name="train_loss", value=2.0))
    db.commit()  # …but a fresh metric proves the worker is alive

    assert claims.claim_next_run(db, backend="mlx", worker_id="thief") is None
    db.refresh(active)
    assert active.status == RunStatus.RUNNING
    assert active.claimed_by == "remote-gpu"


# ---------------------------------------------------------------------------
# A5 — startup recovery semantics
# ---------------------------------------------------------------------------

def test_release_expired_claims_requeues_legacy_rows(db: Session) -> None:
    legacy = _mk_run(db)
    legacy.status = RunStatus.RUNNING       # running but never claimed (pre-R)
    db.add(legacy)
    db.commit()

    released = claims.release_expired_claims(db, include_legacy=True)
    assert released == 1
    db.refresh(legacy)
    assert legacy.status == RunStatus.QUEUED


def test_release_preserves_active_remote_run(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(claims.CLAIM_TIMEOUT_ENV, "60")
    active = _mk_run(db)
    claims.claim_next_run(db, backend="mlx", worker_id="remote-gpu")

    released = claims.release_expired_claims(db, include_legacy=True)
    assert released == 0
    db.refresh(active)
    assert active.status == RunStatus.RUNNING
