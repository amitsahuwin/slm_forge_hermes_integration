"""Phase D — every create endpoint MUST stamp tenant_id + user_id from
the JWT-derived Identity, and MUST NOT honour client-supplied values
for those fields.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from apps.api.models.run import Run, RunMethod
from apps.api.models.session import TrainingSession
from apps.api.routers import runs as runs_router
from apps.api.routers import sessions as sessions_router
from tests.api._isolation_helpers import fresh_engine, make_request


@pytest.fixture()
def engine(tmp_path):
    eng = fresh_engine(tmp_path, "stamp_identity", [TrainingSession, Run])
    yield eng
    eng.dispose()


@pytest.mark.asyncio
async def test_create_run_stamps_caller_identity(engine):
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    payload = runs_router.RunCreate(
        dataset="example/train.jsonl",
        base_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
        method=RunMethod.LORA,
        iters=5,
    )
    with Session(engine) as db:
        created = await runs_router.create_run(  # type: ignore[call-arg]
            payload=payload, request=req, session=db,
        )
        assert created.tenant_id == "acme"
        assert created.user_id == "alice@acme"
        assert created.role == "data_engineer"

    # Re-read from DB to confirm persisted (not just attached to the response).
    with Session(engine) as db:
        row = db.exec(select(Run)).one()
        assert row.tenant_id == "acme"
        assert row.user_id == "alice@acme"


def test_create_session_stamps_caller_identity(engine):
    req = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    payload = sessions_router.SessionCreate(
        name="acme-session-1",
        dataset="example/train.jsonl",
        base_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
    )
    with Session(engine) as db:
        # Bypass the @requires(create, experiment) RBAC decorator — this
        # test exercises identity stamping, not authorization.
        created = sessions_router.create_session.__wrapped__(  # type: ignore[attr-defined]
            payload=payload, request=req, db=db,
        )
        assert created.tenant_id == "acme"
        assert created.user_id == "alice@acme"