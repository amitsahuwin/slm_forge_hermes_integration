"""Phase C — `scope_query` adds tenant + owner WHERE clauses.

Behaviour matrix:

  * admin in tenant X     →  rows where tenant_id == X
  * non-admin in tenant X →  rows where tenant_id == X AND user_id == self
  * worker                →  rows where claimed_by == self (workers
                              operate on runs they have claimed; they
                              do not enumerate by tenant)

The helper is a thin shim over a SQLModel ``select()`` — easier than
threading the same WHERE clause through every router by hand.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.middleware.auth import User
from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.models.session import TrainingSession  # FK target for Run.session_id


@pytest.fixture()
def engine_with_runs(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'scope.db'}")
    # Only create the two tables we need — the rest of SQLModel.metadata
    # carries FKs to tables we haven't included here.
    SQLModel.metadata.create_all(
        eng,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )
    # Seed 6 runs across 2 tenants × 2 users + 1 legacy NULL.
    rows = [
        # tenant=acme
        Run(
            dataset="d1", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="alice", role="data_engineer",
        ),
        Run(
            dataset="d1", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="alice", role="data_engineer",
        ),
        Run(
            dataset="d2", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="bob", role="data_engineer",
        ),
        # tenant=globex
        Run(
            dataset="d3", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="globex", user_id="carol", role="admin",
        ),
        Run(
            dataset="d3", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="globex", user_id="dave", role="data_engineer",
        ),
        # legacy NULL
        Run(
            dataset="d4", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
        ),
    ]
    with Session(eng) as s:
        for r in rows:
            s.add(r)
        s.commit()
    yield eng
    eng.dispose()


def _identity(tenant: str, user: str, role: str):
    from apps.api.services.identity import Identity

    u = User(id=user, roles=[role], groups=[f"/tenants/{tenant}"])
    return Identity.from_user(u)


def test_non_admin_sees_only_own_rows_in_their_tenant(engine_with_runs):
    from apps.api.services.scoping import scope_query

    alice = _identity("acme", "alice", "data_engineer")
    with Session(engine_with_runs) as s:
        rows = list(s.exec(scope_query(select(Run), alice, Run)).all())
    assert len(rows) == 2
    assert all(r.tenant_id == "acme" and r.user_id == "alice" for r in rows)


def test_admin_sees_all_rows_in_their_tenant_only(engine_with_runs):
    from apps.api.services.scoping import scope_query

    admin = _identity("acme", "alice", "admin")
    with Session(engine_with_runs) as s:
        rows = list(s.exec(scope_query(select(Run), admin, Run)).all())
    # Alice (admin) sees both alice rows + bob row, but NOT globex rows or legacy NULL.
    assert len(rows) == 3
    assert all(r.tenant_id == "acme" for r in rows)


def test_other_tenant_cannot_see_acme_rows(engine_with_runs):
    from apps.api.services.scoping import scope_query

    carol = _identity("globex", "carol", "admin")
    with Session(engine_with_runs) as s:
        rows = list(s.exec(scope_query(select(Run), carol, Run)).all())
    assert len(rows) == 2
    assert all(r.tenant_id == "globex" for r in rows)


def test_legacy_null_tenant_rows_invisible_to_all_modern_users(engine_with_runs):
    """Backfilled NULL rows must NOT silently leak across tenants. They
    require an explicit admin claim (out of scope for the scope helper)."""
    from apps.api.services.scoping import scope_query

    for tenant, user, role in [
        ("acme", "alice", "admin"),
        ("acme", "bob", "data_engineer"),
        ("globex", "carol", "admin"),
    ]:
        ident = _identity(tenant, user, role)
        with Session(engine_with_runs) as s:
            rows = list(s.exec(scope_query(select(Run), ident, Run)).all())
        assert all(r.tenant_id is not None for r in rows), (
            f"NULL-tenant row leaked to {tenant}/{user}/{role}"
        )


def test_worker_does_not_see_anything_via_scope_query(engine_with_runs):
    """Workers don't list runs through the normal scope; they consume
    the claim queue. The scope_query helper must refuse worker identity
    so a forgotten `scope_query(...)` in a list endpoint can't be a
    cross-tenant escape hatch."""
    from apps.api.services.scoping import scope_query

    worker = _identity("system", "trainer-bot", "worker")
    with Session(engine_with_runs) as s:
        rows = list(s.exec(scope_query(select(Run), worker, Run)).all())
    assert rows == []
