"""Phase B — `stream_agent` emits a parent agent span; child skill spans
written by `packages.ratchet.hermes_bridge._record_trace` while the
agent context is active must inherit the agent's ``trace_id`` and
record the agent span as ``parent_span_id``.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def trace_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_trace.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url, connect_args={"check_same_thread": False})
    from apps.api.models.hermes_trace import HermesTrace  # noqa: F401
    from apps.api.services import db as db_mod

    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_mod, "engine", eng)
    yield eng
    eng.dispose()


def test_skill_record_under_agent_inherits_trace_id(trace_engine):
    """When ``_record_trace`` is called while a ``trace_span(kind='agent')``
    is active, the persisted row must carry the same ``trace_id`` and the
    agent's ``span_id`` as ``parent_span_id``."""
    from apps.api.models.hermes_trace import HermesTrace
    from apps.api.services.tracing import trace_span
    from packages.ratchet.hermes_bridge import _record_trace

    with trace_span(kind="agent", name="experiment_recommender") as agent:
        _record_trace(
            source="skill:propose_hyperparam_mutation",
            request_body={"hint": "warm up"},
            response_text="{}",
            error=None,
            duration_ms=42,
            skill_name="propose_hyperparam_mutation",
        )

    with Session(trace_engine) as s:
        rows = list(s.exec(select(HermesTrace).order_by(HermesTrace.id)))
    # 1 row from trace_span open/close + 1 from _record_trace = 2 total
    assert len(rows) == 2
    skill_row = next(r for r in rows if r.source.startswith("skill:"))
    assert skill_row.trace_id == agent.trace_id
    assert skill_row.parent_span_id == agent.span_id
    assert skill_row.kind == "skill"


def test_skill_record_outside_agent_remains_root(trace_engine):
    """Backwards compatibility: without an active agent context, a skill
    trace is still its own root (trace_id may be NULL or self)."""
    from apps.api.models.hermes_trace import HermesTrace
    from packages.ratchet.hermes_bridge import _record_trace

    _record_trace(
        source="skill:standalone",
        request_body={},
        response_text="{}",
        error=None,
        duration_ms=10,
        skill_name="standalone",
    )
    with Session(trace_engine) as s:
        rows = list(s.exec(select(HermesTrace)))
    assert len(rows) == 1
    assert rows[0].parent_span_id is None