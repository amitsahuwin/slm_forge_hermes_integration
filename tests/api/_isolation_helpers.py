"""Phase D — shared fixtures/helpers for the per-user isolation test suite.

Every isolation test follows the same shape:

1. Build a fresh in-memory SQLite engine with the SQLModel tables it needs.
2. Mock a ``Request`` whose ``request.state.user`` carries a
   :class:`apps.api.middleware.auth.User` with the right groups + roles
   (so ``current_identity(request)`` resolves to the desired identity).
3. Drive the router function directly (no httpx round-trip).

These helpers consolidate that boilerplate so individual tests stay focused.
"""
from __future__ import annotations

from collections.abc import Iterable
from unittest.mock import MagicMock

from fastapi import Request
from sqlmodel import Session, SQLModel, create_engine

from apps.api.middleware.auth import User


def make_request(*, user_id: str, tenant: str, role: str = "data_engineer") -> Request:
    """Return a ``MagicMock(spec=Request)`` whose ``state.user`` is wired to
    the requested identity."""
    req = MagicMock(spec=Request)
    req.state.user = User(
        id=user_id,
        email=f"{user_id}@{tenant}.test",
        roles=[role],
        groups=[f"/tenants/{tenant}"],
    )
    return req


def synth_admin_request() -> Request:
    """Synthetic-admin request used by non-Phase-D tests that just need to
    satisfy the post-Phase-D ``current_identity(request)`` contract without
    caring about specific identity values. Mirrors ``apps/api/middleware/auth._synthetic_admin``."""
    return make_request(user_id="local-admin", tenant="local", role="admin")


def make_worker_request(worker_id: str = "trainer-bot") -> Request:
    """Workers carry ``role=worker`` and group ``/tenants/system``."""
    req = MagicMock(spec=Request)
    req.state.user = User(
        id=worker_id,
        email=None,
        roles=["worker"],
        groups=["/tenants/system"],
    )
    return req


def fresh_engine(tmp_path, name: str, tables: Iterable[type[SQLModel]]):
    """Create a temp SQLite engine with just the requested tables."""
    eng = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[t.__table__ for t in tables],  # type: ignore[arg-type]
    )
    return eng


def db_session(engine) -> Session:
    return Session(engine)