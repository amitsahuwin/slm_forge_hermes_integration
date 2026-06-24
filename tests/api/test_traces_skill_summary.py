"""Skill-level summary for the Traces tab's left sidebar.

``GET /api/v1/hermes/traces/skills/summary`` aggregates the trace table
into one row per skill, so the UI can show "select_method_for_task —
12 calls, 8 % errors, avg 410 ms" at a glance and surface lifecycle
events ("hash changed 3 times").
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.hermes_trace import HermesTrace
from apps.api.routers.traces import list_skill_summary
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture(autouse=True)
def _reset_auth_cache(monkeypatch: pytest.MonkeyPatch):
    """Insulate against pre-existing auth-cache pollution between test files."""
    monkeypatch.delenv("SLM_FORGE_AUTH_ENABLED", raising=False)
    auth_settings_module.get_auth_settings.cache_clear()
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def engine_with_seeds(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'summary.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    base = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)

    def mk(skill, mins, dur, err, sha):
        return HermesTrace(
            created_at=base + timedelta(minutes=mins),
            source=f"skill:{skill}",
            skill_name=skill,
            skill_sha256=sha,
            model="qwen3:30b-a3b",
            duration_ms=dur,
            error=err,
            success=err is None,
            tenant_id="default",
        )

    rows = [
        # propose: 4 calls, 1 error, hash changes twice (A→A→B→C)
        mk("propose_hyperparam_mutation", 0, 100, None, "aaaaaaaaaaaaaaaa"),
        mk("propose_hyperparam_mutation", 1, 200, None, "aaaaaaaaaaaaaaaa"),
        mk("propose_hyperparam_mutation", 2, 300, "boom", "bbbbbbbbbbbbbbbb"),
        mk("propose_hyperparam_mutation", 3, 400, None, "cccccccccccccccc"),
        # data_quality: 1 call, no errors, no hash change
        mk("data_quality_review", 4, 1500, None, "dddddddddddddddd"),
        # chat: no skill_name → should NOT appear in the summary
        HermesTrace(
            created_at=base + timedelta(minutes=5),
            source="chat",
            skill_name=None,
            skill_sha256=None,
            model="qwen3:30b-a3b",
            duration_ms=10,
            error=None,
            success=True,
            tenant_id="default",
        ),
    ]
    with Session(eng) as s:
        for r in rows:
            s.add(r)
        s.commit()
    return eng


def _req():
    return MagicMock()


def test_summary_groups_by_skill_name(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        result = list_skill_summary(_req(), db)
    skills = {r.skill_name for r in result}
    assert skills == {"propose_hyperparam_mutation", "data_quality_review"}


def test_summary_counts_calls_and_errors(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = {r.skill_name: r for r in list_skill_summary(_req(), db)}
    propose = rows["propose_hyperparam_mutation"]
    assert propose.calls == 4
    assert propose.errors == 1
    dq = rows["data_quality_review"]
    assert dq.calls == 1
    assert dq.errors == 0


def test_summary_avg_duration(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = {r.skill_name: r for r in list_skill_summary(_req(), db)}
    propose = rows["propose_hyperparam_mutation"]
    # (100 + 200 + 300 + 400) / 4 = 250
    assert propose.avg_duration_ms == 250


def test_summary_first_and_last_seen(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = {r.skill_name: r for r in list_skill_summary(_req(), db)}
    propose = rows["propose_hyperparam_mutation"]
    assert propose.first_seen.startswith("2026-06-24T12:00:00")
    assert propose.last_seen.startswith("2026-06-24T12:03:00")


def test_summary_current_sha256_is_latest(engine_with_seeds) -> None:
    """The "current" hash is whatever the latest call observed — that's
    the version Hermes is using right now."""
    with Session(engine_with_seeds) as db:
        rows = {r.skill_name: r for r in list_skill_summary(_req(), db)}
    assert rows["propose_hyperparam_mutation"].current_sha256 == "cccccccccccccccc"
    assert rows["data_quality_review"].current_sha256 == "dddddddddddddddd"


def test_summary_change_count(engine_with_seeds) -> None:
    """Hash transitions: A→A (no), A→B (yes), B→C (yes) → 2 changes."""
    with Session(engine_with_seeds) as db:
        rows = {r.skill_name: r for r in list_skill_summary(_req(), db)}
    assert rows["propose_hyperparam_mutation"].change_count == 2
    assert rows["data_quality_review"].change_count == 0


def test_summary_excludes_non_skill_rows(engine_with_seeds) -> None:
    """``source='chat'`` has no ``skill_name`` and must not show up — the
    sidebar is per-skill, not per-source."""
    with Session(engine_with_seeds) as db:
        result = list_skill_summary(_req(), db)
    assert all(r.skill_name for r in result)


def test_summary_empty_table_returns_empty_list(tmp_path, monkeypatch) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    with Session(eng) as db:
        result = list_skill_summary(_req(), db)
    assert result == []
