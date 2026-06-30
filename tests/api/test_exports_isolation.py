"""Phase D — cross-user / cross-tenant isolation on /api/v1/exports."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from apps.api.models.export import Export, ExportStatus
from apps.api.models.run import Run
from apps.api.models.session import TrainingSession
from apps.api.routers import exports as exports_router
from tests.api._isolation_helpers import fresh_engine, make_request


def _seed(engine) -> dict[str, int]:
    # Need a parent Run for the FK; tenant doesn't matter for the FK
    # itself since exports carry their own tenant_id.
    with Session(engine) as s:
        parent = Run(
            dataset="d", base_model="m", iters=10, batch_size=1,
            learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0,
            tenant_id="acme", user_id="alice@acme", role="data_engineer",
        )
        s.add(parent)
        s.commit()
        s.refresh(parent)
        assert parent.id is not None
        run_id = parent.id

        rows = {
            "alice_acme": Export(
                run_id=run_id, base_model="m", method="lora",
                status=ExportStatus.QUEUED,
                tenant_id="acme", user_id="alice@acme", role="data_engineer",
            ),
            "bob_acme": Export(
                run_id=run_id, base_model="m", method="lora",
                status=ExportStatus.QUEUED,
                tenant_id="acme", user_id="bob@acme", role="data_engineer",
            ),
            "admin_local": Export(
                run_id=run_id, base_model="m", method="lora",
                status=ExportStatus.QUEUED,
                tenant_id="local", user_id="admin@local", role="admin",
            ),
        }
        for r in rows.values():
            s.add(r)
        s.commit()
        out: dict[str, int] = {}
        for k, r in rows.items():
            s.refresh(r)
            assert r.id is not None
            out[k] = r.id
        return out


@pytest.fixture()
def engine(tmp_path):
    eng = fresh_engine(tmp_path, "exports_isolation", [TrainingSession, Run, Export])
    yield eng
    eng.dispose()


def test_alice_lists_only_her_own_exports(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db:
        result = exports_router.list_exports(request=req, db=db, status=None, limit=200)  # type: ignore[call-arg]
    seen = {r.id for r in result}
    assert ids["alice_acme"] in seen
    assert ids["bob_acme"] not in seen
    assert ids["admin_local"] not in seen


def test_alice_cannot_get_bob_export(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db, pytest.raises(HTTPException) as exc:
        exports_router.get_export(  # type: ignore[call-arg]
            xid=ids["bob_acme"], request=req, db=db,
        )
    assert exc.value.status_code == 404