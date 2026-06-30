"""Phase B — `GET /api/v1/hermes/traces?group_by=trace` returns a tree.

Default (`group_by=none`) is the existing flat list — backwards
compatible. When `group_by=trace`, rows that share a `trace_id` are
collapsed into a single root row with `children: list[TraceRow]`,
ordered by `created_at`. New endpoint `/by-trace/{trace_id}` returns
the full tree for one trace.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.middleware import auth as auth_module
from apps.api.middleware.auth import User
from apps.api.models.hermes_trace import HermesTrace
from apps.api.routers.traces import (
    get_trace_tree,
    list_traces,
)
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture()
def admin_request():
    auth_settings_module.get_auth_settings.cache_clear()
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def allow_all_policy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (True, ""),
    )


@pytest.fixture()
def engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'traces.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def _req(user_roles: list[str]) -> MagicMock:
    # Phase D — group binds Identity.tenant_id to "default" so the
    # caller can see seed rows that carry tenant_id="default".
    req = MagicMock()
    req.state.user = User(
        id="default" if "admin" in user_roles else "alice",
        email="alice@x",
        roles=user_roles,
        groups=["/tenants/default"],
    )
    return req


def _seed(engine, n_traces: int = 2, skills_per_trace: int = 3) -> list[str]:
    """Insert n agent traces, each with k child skill rows. Returns trace_ids."""
    now = datetime.now(UTC)
    trace_ids: list[str] = []
    with Session(engine) as s:
        for t in range(n_traces):
            tid = f"trace-{t:08x}"
            trace_ids.append(tid)
            agent_span = HermesTrace(
                source=f"agent_{t}",
                model="hermes",
                request_body="{}",
                response_body="{}",
                duration_ms=100 + t,
                kind="agent",
                trace_id=tid,
                span_id=f"agent-span-{t}",
                parent_span_id=None,
                agent_run_id=f"ar-{t}",
                created_at=now + timedelta(milliseconds=t * 10),
            )
            s.add(agent_span)
            s.commit()
            s.refresh(agent_span)
            for k in range(skills_per_trace):
                child = HermesTrace(
                    source=f"skill_{t}_{k}",
                    model="hermes",
                    request_body="{}",
                    response_body="{}",
                    duration_ms=10 + k,
                    kind="skill",
                    trace_id=tid,
                    span_id=f"skill-span-{t}-{k}",
                    parent_span_id=agent_span.span_id,
                    agent_run_id=f"ar-{t}",
                    created_at=now + timedelta(milliseconds=t * 10 + k + 1),
                )
                s.add(child)
            s.commit()
    return trace_ids


def test_group_by_trace_returns_tree(admin_request, allow_all_policy, engine):
    _seed(engine, n_traces=2, skills_per_trace=3)
    with Session(engine) as db:
        result = list_traces(request=_req(["admin"]), db=db, group_by="trace")  # type: ignore[call-arg]
    # Trees, one per trace
    assert isinstance(result, list)
    assert len(result) == 2
    for tree in result:
        # Each tree row is a dict-like object with `children`
        assert getattr(tree, "kind", None) == "agent"
        children = getattr(tree, "children", None)
        assert children is not None
        assert len(children) == 3
        for c in children:
            assert c.kind == "skill"
            assert c.trace_id == tree.trace_id
            assert c.parent_span_id == tree.span_id


def test_group_by_none_returns_flat_list_backwards_compatible(
    admin_request, allow_all_policy, engine
):
    _seed(engine, n_traces=2, skills_per_trace=3)
    with Session(engine) as db:
        result = list_traces(request=_req(["admin"]), db=db)  # type: ignore[call-arg]
    assert len(result) == 2 * 4  # agent + 3 skills, twice
    # The original TraceRow shape — no `children` field
    assert all(not hasattr(r, "children") or r.children is None for r in result)


def test_kind_filter_narrows_to_agents_only(admin_request, allow_all_policy, engine):
    _seed(engine, n_traces=2, skills_per_trace=3)
    with Session(engine) as db:
        result = list_traces(request=_req(["admin"]), db=db, kind="agent")  # type: ignore[call-arg]
    assert len(result) == 2
    assert all(r.kind == "agent" for r in result)


def test_by_trace_endpoint_returns_one_tree(admin_request, allow_all_policy, engine):
    trace_ids = _seed(engine, n_traces=2, skills_per_trace=3)
    with Session(engine) as db:
        tree = get_trace_tree(trace_id=trace_ids[0], request=_req(["admin"]), db=db)  # type: ignore[call-arg]
    assert tree.trace_id == trace_ids[0]
    assert tree.kind == "agent"
    assert len(tree.children) == 3


def test_by_trace_endpoint_404_on_unknown(admin_request, allow_all_policy, engine):
    from fastapi import HTTPException

    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_trace_tree(trace_id="does-not-exist", request=_req(["admin"]), db=db)  # type: ignore[call-arg]
    assert ei.value.status_code == 404