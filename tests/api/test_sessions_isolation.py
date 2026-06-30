"""Phase D — cross-user / cross-tenant isolation on /api/v1/sessions."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from apps.api.models.run import Run  # included to satisfy FK metadata
from apps.api.models.session import SessionStatus, TrainingSession
from apps.api.routers import sessions as sessions_router
from tests.api._isolation_helpers import fresh_engine, make_request


def _seed(engine) -> dict[str, int]:
    rows = {
        "alice_acme": TrainingSession(
            name="alice-s1", dataset="d", base_model="m",
            status=SessionStatus.QUEUED,
            tenant_id="acme", user_id="alice@acme", role="data_engineer",
        ),
        "bob_acme": TrainingSession(
            name="bob-s1", dataset="d", base_model="m",
            status=SessionStatus.QUEUED,
            tenant_id="acme", user_id="bob@acme", role="data_engineer",
        ),
        "admin_local": TrainingSession(
            name="admin-local-s1", dataset="d", base_model="m",
            status=SessionStatus.QUEUED,
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
    eng = fresh_engine(tmp_path, "sessions_isolation", [TrainingSession, Run])
    yield eng
    eng.dispose()


def test_alice_lists_only_her_own(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db:
        result = sessions_router.list_sessions(request=req, db=db, status=None, limit=200)  # type: ignore[call-arg]
    seen = {r.id for r in result}
    assert ids["alice_acme"] in seen
    assert ids["bob_acme"] not in seen
    assert ids["admin_local"] not in seen


def test_alice_cannot_get_admin_locals_session(engine):
    ids = _seed(engine)
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    with Session(engine) as db, pytest.raises(HTTPException) as exc:
        sessions_router.get_session_(  # type: ignore[attr-defined,call-arg]
            sid=ids["admin_local"], request=req, db=db,
        )
    assert exc.value.status_code == 404