"""Phase D — cross-user / cross-tenant isolation on /api/v1/runs.

``alice@acme`` MUST NOT see ``bob@acme``'s runs (per-user boundary).
``alice@acme`` MUST NOT see ``admin@local``'s runs (per-tenant boundary).
``admin@acme`` (admin role within same tenant) MAY see all acme runs.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.models.session import TrainingSession  # FK target for Run.session_id
from apps.api.routers import runs as runs_router
from tests.api._isolation_helpers import fresh_engine, make_request, make_worker_request


def _seed(engine) -> dict[str, int]:
    """Seed canonical fixture rows and return a name→id map."""
    rows = {
        "alice_acme_1": Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="alice@acme", role="data_engineer",
        ),
        "alice_acme_2": Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="alice@acme", role="data_engineer",
        ),
        "bob_acme": Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="acme", user_id="bob@acme", role="data_engineer",
        ),
        "admin_local": Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.QUEUED,
            tenant_id="local", user_id="admin@local", role="admin",
        ),
    }
    out: dict[str, int] = {}
    with Session(engine) as s:
        for r in rows.values():
            s.add(r)
        s.commit()
        for k, r in rows.items():
            s.refresh(r)
            assert r.id is not None
            out[k] = r.id
    return out


@pytest.fixture()
def engine(tmp_path):
    eng = fresh_engine(tmp_path, "runs_isolation", [TrainingSession, Run])
    yield eng
    eng.dispose()


def test_alice_lists_only_her_own_acme_runs(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db:
        result = runs_router.list_runs(request=req, session=db, status=None, backend=None, limit=200)  # type: ignore[call-arg]
    seen = {r.id for r in result}
    assert ids["alice_acme_1"] in seen
    assert ids["alice_acme_2"] in seen
    assert ids["bob_acme"] not in seen, "alice should not see bob's runs (per-user)"
    assert ids["admin_local"] not in seen, "alice should not see local-tenant runs"


def test_admin_acme_sees_all_acme_runs_not_local(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="admin")
    with Session(engine) as db:
        result = runs_router.list_runs(request=req, session=db, status=None, backend=None, limit=200)  # type: ignore[call-arg]
    seen = {r.id for r in result}
    assert ids["alice_acme_1"] in seen
    assert ids["alice_acme_2"] in seen
    assert ids["bob_acme"] in seen, "tenant-admin should see all acme runs"
    assert ids["admin_local"] not in seen, "tenant-admin must not cross tenants"


def test_alice_cannot_get_bobs_run_by_id(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db, pytest.raises(HTTPException) as exc:
        runs_router.get_run(run_id=ids["bob_acme"], request=req, session=db)  # type: ignore[call-arg]
    assert exc.value.status_code == 404


def test_alice_cannot_patch_bobs_run(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db, pytest.raises(HTTPException) as exc:
        runs_router.patch_run(  # type: ignore[call-arg]
            run_id=ids["bob_acme"],
            payload=runs_router.RunPatch(status=RunStatus.CANCELLED),
            request=req,
            session=db,
            background=None,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 404


def test_worker_request_returns_empty_list_via_router(engine):
    """Workers must not enumerate via the list endpoint. The claim
    endpoint is the only path that returns rows for a worker."""
    _seed(engine)
    req = make_worker_request("trainer-bot")
    with Session(engine) as db:
        result = runs_router.list_runs(request=req, session=db, status=None, backend=None, limit=200)  # type: ignore[call-arg]
    assert result == []