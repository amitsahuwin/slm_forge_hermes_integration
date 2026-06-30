"""Phase D — workers are system-level. The claim queue MUST NOT be
tenant-scoped: a single CUDA worker should be able to claim a queued
run from acme, globex, or local, in FIFO order.

This is the explicit by-design exception to the tenant boundary. The
upload path then stamps the artifact under the *claimed Run's*
tenant_id/user_id, not the worker's.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session

from apps.api.models.metric import Metric  # claim_next_run sweeps metrics on lease recovery
from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.models.session import TrainingSession
from apps.api.routers import runs as runs_router
from tests.api._isolation_helpers import fresh_engine


@pytest.fixture()
def engine(tmp_path):
    eng = fresh_engine(tmp_path, "worker_claim", [TrainingSession, Run, Metric])
    yield eng
    eng.dispose()


def _queued(*, tenant: str, user: str, backend: str = "mlx") -> Run:
    return Run(
        dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
        batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
        grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
        trainer_backend=backend,
        tenant_id=tenant, user_id=user, role="data_engineer",
    )


def test_worker_claims_from_any_tenant(engine):
    with Session(engine) as db:
        db.add(_queued(tenant="acme", user="alice@acme"))
        db.add(_queued(tenant="globex", user="carol@globex"))
        db.add(_queued(tenant="local", user="admin@local"))
        db.commit()

    payload = runs_router.RunClaim(backend="mlx", worker_id="trainer-bot")

    claimed_tenants: set[str | None] = set()
    for _ in range(3):
        with Session(engine) as db:
            claimed = runs_router.claim_run(payload=payload, session=db)
        assert claimed is not None, "queue should still have queued runs"
        claimed_tenants.add(claimed.tenant_id)

    assert claimed_tenants == {"acme", "globex", "local"}


def test_claim_returns_none_when_queue_empty(engine):
    payload = runs_router.RunClaim(backend="mlx", worker_id="trainer-bot")
    with Session(engine) as db:
        assert runs_router.claim_run(payload=payload, session=db) is None