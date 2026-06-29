"""Phase B — trace_span contextvar-based nesting for hermes_traces.

The Traces tab today only shows skill spans because every row is its own
isolated record. Phase B introduces a tiny `trace_span` context manager
that:

  * gives every span a `trace_id` (root or inherited via contextvar),
  * records the immediate parent as `parent_span_id`,
  * tags each row with `kind` (agent | skill | tool),
  * carries an `agent_run_id` so an entire agent invocation can be
    located in one query.

Tests assert behaviour against an in-memory SQLite, so they do not
touch the real `/app/data/slm_forge.db`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def trace_engine(tmp_path, monkeypatch):
    """A throwaway SQLite for HermesTrace; isolates from the real DB."""
    db_path = tmp_path / "trace.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("SLM_FORGE_DB_URL", url)

    # Re-import to pick up the env var.
    from apps.api.models.hermes_trace import HermesTrace  # noqa: F401
    from apps.api.services import db as db_mod

    engine = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_mod, "engine", engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _rows(engine) -> list:
    from apps.api.models.hermes_trace import HermesTrace

    with Session(engine) as s:
        return list(s.exec(select(HermesTrace).order_by(HermesTrace.id)))


def test_root_span_assigns_trace_id_and_null_parent(trace_engine):
    from apps.api.services.tracing import trace_span

    with trace_span(kind="agent", name="experiment_recommender") as span:
        assert span.trace_id  # generated UUID-ish string
        assert span.parent_span_id is None
        assert span.span_id  # generated

    rows = _rows(trace_engine)
    assert len(rows) == 1
    r = rows[0]
    assert r.kind == "agent"
    assert r.source == "experiment_recommender"
    assert r.trace_id == span.trace_id
    assert r.parent_span_id is None


def test_child_span_inherits_trace_id_and_records_parent(trace_engine):
    from apps.api.services.tracing import trace_span

    with trace_span(kind="agent", name="experiment_recommender") as parent:
        with trace_span(kind="skill", name="propose_hyperparam_mutation") as child:
            assert child.trace_id == parent.trace_id
            assert child.parent_span_id == parent.span_id
            assert child.span_id != parent.span_id

    rows = _rows(trace_engine)
    assert len(rows) == 2
    parent_row = next(r for r in rows if r.kind == "agent")
    child_row = next(r for r in rows if r.kind == "skill")
    assert child_row.trace_id == parent_row.trace_id
    assert child_row.parent_span_id == parent_row.span_id


def test_sibling_skill_spans_share_trace_but_not_parent_link(trace_engine):
    from apps.api.services.tracing import trace_span

    with trace_span(kind="agent", name="optimization_coach") as parent:
        with trace_span(kind="skill", name="skill_one") as a:
            pass
        with trace_span(kind="skill", name="skill_two") as b:
            pass
        # both siblings point at the agent span
        assert a.parent_span_id == parent.span_id
        assert b.parent_span_id == parent.span_id
        assert a.trace_id == b.trace_id == parent.trace_id
        assert a.span_id != b.span_id


def test_exception_unwinds_contextvar_stack(trace_engine):
    """A raised exception must still record a row and pop the stack so the
    next root span does not inherit a stale parent."""
    from apps.api.services.tracing import _span_stack, trace_span

    with pytest.raises(RuntimeError), trace_span(kind="agent", name="incident_responder"):
        raise RuntimeError("boom")
    assert _span_stack.get() == ()  # popped

    with trace_span(kind="agent", name="next_agent") as next_root:
        assert next_root.parent_span_id is None  # not stuck inside the failed parent

    rows = _rows(trace_engine)
    assert len(rows) == 2
    failed = next(r for r in rows if r.source == "incident_responder")
    assert failed.error == "boom"
    assert failed.success is False


def test_agent_run_id_propagates_to_children(trace_engine):
    from apps.api.services.tracing import trace_span

    agent_run_id = str(uuid.uuid4())
    with trace_span(kind="agent", name="evaluation_designer", agent_run_id=agent_run_id):
        with trace_span(kind="skill", name="skill_a"):
            pass
        with trace_span(kind="skill", name="skill_b"):
            pass

    rows = _rows(trace_engine)
    assert len(rows) == 3
    for r in rows:
        assert r.agent_run_id == agent_run_id


def test_set_result_captures_response_body(trace_engine):
    from apps.api.services.tracing import trace_span

    with trace_span(kind="agent", name="experiment_recommender") as span:
        span.set_result({"recommendation": "lr=3e-5"})

    rows = _rows(trace_engine)
    assert len(rows) == 1
    assert "recommendation" in rows[0].response_body
    assert "3e-5" in rows[0].response_body
