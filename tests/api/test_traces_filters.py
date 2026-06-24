"""Expanded filters on ``GET /api/v1/hermes/traces``.

The current endpoint only supports ``source_like`` substring and ``limit``.
The Skill-Activity view needs:

* ``skill``           repeatable, exact match on the parsed ``skill_name``.
* ``status``          ``"success"`` / ``"error"`` — filters on ``success`` column.
* ``since`` / ``until``  ISO-8601 datetime range on ``created_at``.
* ``min_duration_ms`` int threshold.
* ``run_id`` / ``session_id``  exact match.

The response rows also gain a derived ``skill_changed`` flag: ``True``
when this row's ``skill_sha256`` differs from the previous trace for the
same ``skill_name``. That flag is the user-visible "skill content
changed since last call" signal.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.hermes_trace import HermesTrace
from apps.api.routers.traces import list_traces
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture(autouse=True)
def _reset_auth_cache(monkeypatch: pytest.MonkeyPatch):
    """The auth-settings LRU cache leaks across tests when earlier tests
    leave ``SLM_FORGE_AUTH_ENABLED=true`` in the cache. Clear it + the env
    so these tests always see the default-disabled state.
    """
    monkeypatch.delenv("SLM_FORGE_AUTH_ENABLED", raising=False)
    auth_settings_module.get_auth_settings.cache_clear()
    yield
    auth_settings_module.get_auth_settings.cache_clear()


@pytest.fixture()
def engine_with_seeds(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'filters.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    base = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
    rows = [
        # 0: propose, success, run 1 session 1, hash A
        HermesTrace(
            created_at=base + timedelta(minutes=0),
            source="skill:propose_hyperparam_mutation",
            skill_name="propose_hyperparam_mutation",
            skill_sha256="aaaaaaaaaaaaaaaa",
            model="qwen3:30b-a3b",
            duration_ms=100,
            error=None,
            success=True,
            run_id=1,
            session_id=1,
            tenant_id="default",
        ),
        # 1: propose, success, run 2 session 1, SAME hash A → skill_changed=False
        HermesTrace(
            created_at=base + timedelta(minutes=1),
            source="skill:propose_hyperparam_mutation",
            skill_name="propose_hyperparam_mutation",
            skill_sha256="aaaaaaaaaaaaaaaa",
            model="qwen3:30b-a3b",
            duration_ms=200,
            error=None,
            success=True,
            run_id=2,
            session_id=1,
            tenant_id="default",
        ),
        # 2: propose, error, run 3 session 1, hash B → skill_changed=True
        HermesTrace(
            created_at=base + timedelta(minutes=2),
            source="skill:propose_hyperparam_mutation",
            skill_name="propose_hyperparam_mutation",
            skill_sha256="bbbbbbbbbbbbbbbb",
            model="qwen3:30b-a3b",
            duration_ms=50,
            error="timeout",
            success=False,
            run_id=3,
            session_id=1,
            tenant_id="default",
        ),
        # 3: data_quality_review, success, no run/session
        HermesTrace(
            created_at=base + timedelta(minutes=3),
            source="skill:data_quality_review",
            skill_name="data_quality_review",
            skill_sha256="cccccccccccccccc",
            model="qwen3:30b-a3b",
            duration_ms=1500,
            error=None,
            success=True,
            run_id=None,
            session_id=None,
            tenant_id="default",
        ),
        # 4: chat (no skill_name), success
        HermesTrace(
            created_at=base + timedelta(minutes=4),
            source="chat",
            skill_name=None,
            skill_sha256=None,
            model="qwen3:30b-a3b",
            duration_ms=75,
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


def _req() -> object:
    """``list_traces`` accepts ``request: Request`` only for OPA enforcement;
    when auth is disabled (the test default), the value is ignored."""
    return MagicMock()


# ---------------------------------------------------------------------------
# skill filter (multi-select / repeatable)
# ---------------------------------------------------------------------------


def test_filter_by_single_skill(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, skill=["propose_hyperparam_mutation"])  # type: ignore[call-arg]
    assert {r.skill_name for r in rows} == {"propose_hyperparam_mutation"}
    assert len(rows) == 3


def test_filter_by_multiple_skills(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(
            _req(),
            db,
            skill=["propose_hyperparam_mutation", "data_quality_review"],  # type: ignore[call-arg]
        )
    assert {r.skill_name for r in rows} == {
        "propose_hyperparam_mutation",
        "data_quality_review",
    }


# ---------------------------------------------------------------------------
# status filter
# ---------------------------------------------------------------------------


def test_filter_status_error(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, status="error")  # type: ignore[call-arg]
    assert len(rows) == 1
    assert rows[0].error == "timeout"
    assert rows[0].success is False


def test_filter_status_success(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, status="success")  # type: ignore[call-arg]
    assert all(r.success for r in rows)
    assert len(rows) == 4


# ---------------------------------------------------------------------------
# time range
# ---------------------------------------------------------------------------


def test_filter_since(engine_with_seeds) -> None:
    cutoff = datetime(2026, 6, 24, 12, 2, 30, tzinfo=UTC).isoformat()
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, since=cutoff)  # type: ignore[call-arg]
    # rows at minute 3 and 4 only
    assert {r.skill_name for r in rows} == {"data_quality_review", None}


def test_filter_until(engine_with_seeds) -> None:
    cutoff = datetime(2026, 6, 24, 12, 1, 30, tzinfo=UTC).isoformat()
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, until=cutoff)  # type: ignore[call-arg]
    # rows at minute 0 and 1 only
    assert len(rows) == 2
    assert all(r.skill_name == "propose_hyperparam_mutation" for r in rows)


# ---------------------------------------------------------------------------
# duration + run/session
# ---------------------------------------------------------------------------


def test_filter_min_duration(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, min_duration_ms=1000)  # type: ignore[call-arg]
    assert len(rows) == 1
    assert rows[0].duration_ms == 1500


def test_filter_run_id(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, run_id=2)  # type: ignore[call-arg]
    assert len(rows) == 1
    assert rows[0].run_id == 2


def test_filter_session_id(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, session_id=1)  # type: ignore[call-arg]
    # 3 propose_hyperparam_mutation rows are tied to session 1
    assert {r.skill_name for r in rows} == {"propose_hyperparam_mutation"}
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# skill_changed derived flag
# ---------------------------------------------------------------------------


def test_skill_changed_flag(engine_with_seeds) -> None:
    """Row order returned by the endpoint is *newest first* (created_at desc),
    which matches the current behavior. The flag must compare to the prior
    row for the same skill *chronologically*, not by list order."""
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, skill=["propose_hyperparam_mutation"])  # type: ignore[call-arg]

    # rows[0] = minute 2 (hash B, previous was minute 1 hash A) → True
    # rows[1] = minute 1 (hash A, previous was minute 0 hash A) → False
    # rows[2] = minute 0 (no previous) → False
    by_time = sorted(rows, key=lambda r: r.created_at)
    assert by_time[0].skill_changed is False
    assert by_time[1].skill_changed is False
    assert by_time[2].skill_changed is True


def test_skill_changed_false_for_non_skill_rows(engine_with_seeds) -> None:
    """``source='chat'`` rows have ``skill_name=None``; the flag should be
    ``False`` for them since there's no skill content to track."""
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, source_like="chat")  # type: ignore[call-arg]
    assert len(rows) == 1
    assert rows[0].skill_changed is False


# ---------------------------------------------------------------------------
# row shape — new fields exposed
# ---------------------------------------------------------------------------


def test_response_includes_new_fields(engine_with_seeds) -> None:
    with Session(engine_with_seeds) as db:
        rows = list_traces(_req(), db, skill=["propose_hyperparam_mutation"], limit=1)  # type: ignore[call-arg]
    row = rows[0]
    for attr in (
        "skill_name",
        "skill_sha256",
        "skill_mtime",
        "run_id",
        "session_id",
        "success",
        "skill_changed",
    ):
        assert hasattr(row, attr), f"Response missing {attr!r}"
