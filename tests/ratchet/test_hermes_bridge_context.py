"""Run/session correlation on every Hermes trace.

The contextvars ``run_id_ctx`` and ``session_id_ctx`` already exist in
``packages._log_context`` (used by the JSON log formatter). This change
makes ``_record_trace`` *read* them so the Traces tab can answer
"which run / which session triggered this Hermes call?" without
guessing from timestamps.

CLAUDE.md rule 16 — no silent fallback defaults. If neither contextvar
is bound, the row writes NULL; we never invent a value.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.hermes_trace import HermesTrace
from apps.api.services import db as db_module


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'trace.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def test_record_trace_reads_run_and_session_contextvars(isolated_engine) -> None:
    """When ``bind(run_id=..., session_id=...)`` is in scope, the trace row
    carries the integers. This is what the loop worker sets around each
    Hermes call inside ``run_session``."""
    import packages.ratchet.hermes_bridge as hb
    from packages._log_context import bind, reset

    tokens = bind(run_id=42, session_id=7)
    try:
        hb._record_trace(
            source="skill:propose_hyperparam_mutation",
            request_body={},
            response_text='{"ok": 1}',
            error=None,
            duration_ms=33,
            attempts=1,
        )
    finally:
        reset(tokens)

    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.run_id == 42
        assert row.session_id == 7


def test_unbound_contextvars_persist_null(isolated_engine, monkeypatch) -> None:
    """No fallback. Worker contexts that aren't tied to a run (healthcheck,
    early bootstrap) write NULL, never zero, never -1, never a fabricated
    default. CLAUDE.md rule 16."""
    # Defensive: clear any lingering contextvars from a previous test.
    from packages._log_context import run_id_ctx, session_id_ctx

    # The ContextVar API doesn't support .set(None) cleanly; reset via token.
    rtoken = run_id_ctx.set(None)
    stoken = session_id_ctx.set(None)
    try:
        import packages.ratchet.hermes_bridge as hb

        hb._record_trace(
            source="chat",
            request_body={},
            response_text="",
            error=None,
            duration_ms=1,
            attempts=1,
        )
        with Session(isolated_engine) as s:
            row = s.exec(select(HermesTrace)).one()
            assert row.run_id is None
            assert row.session_id is None
    finally:
        run_id_ctx.reset(rtoken)
        session_id_ctx.reset(stoken)


def test_partial_binding_persists_only_bound_field(isolated_engine) -> None:
    """A worker may know a session but not yet which run was created. The
    bound field lands; the unbound one stays NULL."""
    import packages.ratchet.hermes_bridge as hb
    from packages._log_context import bind, reset

    tokens = bind(session_id=99)
    try:
        hb._record_trace(
            source="skill:propose_canary_set",
            request_body={},
            response_text="",
            error=None,
            duration_ms=5,
            attempts=1,
        )
    finally:
        reset(tokens)

    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.session_id == 99
        assert row.run_id is None


def test_string_ids_are_coerced_to_int(isolated_engine) -> None:
    """``bind`` stringifies for log compatibility; the trace persists ints
    so the foreign-key join (run_id → runs.id) lines up. The bridge must
    coerce string → int and tolerate non-numeric values by writing NULL."""
    import packages.ratchet.hermes_bridge as hb
    from packages._log_context import bind, reset

    # Numeric string — should land as int.
    tokens = bind(run_id="123", session_id="4")
    try:
        hb._record_trace(
            source="skill:demo",
            request_body={},
            response_text="",
            error=None,
            duration_ms=1,
            attempts=1,
        )
    finally:
        reset(tokens)

    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.run_id == 123
        assert row.session_id == 4
