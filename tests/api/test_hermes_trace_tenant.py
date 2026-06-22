"""PR-1 A4 — HermesTrace rows carry a tenant_id.

Worker context (no FastAPI request, no contextvar bound) → tenant resolved
from ``SLM_FORGE_TENANT_ID`` env, falling back to ``SLM_FORGE_DEFAULT_TENANT``,
then literal ``"default"``.

API context (contextvar bound by ``RequestContextMiddleware``) → tenant
comes from the contextvar.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.hermes_trace import HermesTrace
from apps.api.services import db as db_module
from apps.api.services.tenant import current_tenant, default_tenant


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite per test, monkeypatched in place of the global engine.

    Only the HermesTrace table is created — using ``metadata.create_all(eng)``
    with no ``tables=`` filter pulls in every model imported anywhere in the
    test session and trips foreign-key checks (e.g. ``runs.session_id`` ->
    ``sessions.id``) when those tables aren't co-resident.
    """
    eng = create_engine(f"sqlite:///{tmp_path / 'trace.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


def test_default_tenant_returns_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SLM_FORGE_TENANT_ID", raising=False)
    monkeypatch.delenv("SLM_FORGE_DEFAULT_TENANT", raising=False)
    assert default_tenant() == "default"


def test_default_tenant_honours_default_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SLM_FORGE_TENANT_ID", raising=False)
    monkeypatch.setenv("SLM_FORGE_DEFAULT_TENANT", "acme")
    assert default_tenant() == "acme"


def test_per_worker_env_overrides_default(monkeypatch: pytest.MonkeyPatch):
    """``SLM_FORGE_TENANT_ID`` is the per-worker override; takes priority."""
    monkeypatch.setenv("SLM_FORGE_TENANT_ID", "team-a")
    monkeypatch.setenv("SLM_FORGE_DEFAULT_TENANT", "ignored")
    assert default_tenant() == "team-a"


def test_current_tenant_reads_contextvar(monkeypatch: pytest.MonkeyPatch):
    """API request context (contextvar bound) takes priority over env defaults."""
    from packages._log_context import bind, reset

    monkeypatch.delenv("SLM_FORGE_TENANT_ID", raising=False)
    monkeypatch.delenv("SLM_FORGE_DEFAULT_TENANT", raising=False)

    tokens = bind(tenant_id="api-tenant-77")
    try:
        assert current_tenant() == "api-tenant-77"
    finally:
        reset(tokens)


def test_current_tenant_falls_back_when_contextvar_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLM_FORGE_TENANT_ID", "worker-bound")
    assert current_tenant() == "worker-bound"


def test_record_trace_persists_tenant_from_env(
    monkeypatch: pytest.MonkeyPatch, isolated_engine
):
    """Worker path: ``_record_trace`` uses ``current_tenant()``, which falls
    through to the env value when no contextvar is bound."""
    monkeypatch.setenv("SLM_FORGE_TENANT_ID", "acme")
    import packages.ratchet.hermes_bridge as hb

    hb._record_trace(
        source="chat",
        request_body={"foo": "bar"},
        response_text='{"ok":1}',
        error=None,
        duration_ms=42,
        attempts=1,
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(HermesTrace)).all()
        assert len(rows) == 1
        assert rows[0].tenant_id == "acme"
        assert rows[0].attempts == 1


def test_record_trace_default_tenant_when_unset(
    monkeypatch: pytest.MonkeyPatch, isolated_engine
):
    monkeypatch.delenv("SLM_FORGE_TENANT_ID", raising=False)
    monkeypatch.delenv("SLM_FORGE_DEFAULT_TENANT", raising=False)
    import packages.ratchet.hermes_bridge as hb

    hb._record_trace(
        source="chat",
        request_body={},
        response_text="",
        error=None,
        duration_ms=1,
        attempts=2,
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(HermesTrace)).all()
        assert rows[0].tenant_id == "default"
        assert rows[0].attempts == 2
