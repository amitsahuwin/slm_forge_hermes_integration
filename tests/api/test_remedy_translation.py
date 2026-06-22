"""PR-3 — plain-English remedy attached to 422/4xx errors.

Pin the contract:

  - Catalog-rejected 422 → ``detail = {"message": str, "remedy": str|None}``.
  - Hermes down / timed out → original 422 returned with ``remedy=None``.
  - Same input twice → second call is a cache hit (no Hermes invocation).
  - ``HERMES_REMEDY_ENABLED=false`` → no Hermes call, ``remedy=None``.
  - The remedy hop must NEVER raise into the error path.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.run import Run
from apps.api.models.session import TrainingSession  # FK target for Run.session_id
from apps.api.routers.runs import RunCreate, create_run
from apps.api.services import remedy as remedy_module


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'remedy.db'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch: pytest.MonkeyPatch):
    remedy_module.clear_cache()
    monkeypatch.delenv("HERMES_REMEDY_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_REMEDY_TIMEOUT_S", raising=False)
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    yield
    remedy_module.clear_cache()


@pytest.mark.asyncio
async def test_create_run_uncataloged_returns_remedy_in_detail(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Catalog rejection → 422 with `detail.remedy` populated."""

    def fake_invoke(payload):
        return "Pick mlx-community/Qwen2.5-3B-Instruct-4bit instead."

    monkeypatch.setattr(remedy_module, "_invoke_skill", fake_invoke)

    with pytest.raises(HTTPException) as ei:
        await create_run(
            RunCreate(dataset="d", base_model="totally/made-up"), db_session
        )

    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert "message" in detail
    assert detail["remedy"] == "Pick mlx-community/Qwen2.5-3B-Instruct-4bit instead."


@pytest.mark.asyncio
async def test_remedy_none_when_hermes_errors(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Hermes blowing up does NOT leak — `detail.remedy=None`, original message intact."""
    import httpx

    def explode(payload):
        raise httpx.ConnectError("ollama down")

    monkeypatch.setattr(remedy_module, "_invoke_skill", explode)

    with pytest.raises(HTTPException) as ei:
        await create_run(
            RunCreate(dataset="d", base_model="totally/made-up"), db_session
        )

    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail["remedy"] is None
    assert "totally/made-up" in detail["message"]


@pytest.mark.asyncio
async def test_remedy_times_out_under_cap(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Slow Hermes → asyncio.wait_for cap fires, remedy=None, original 422 returned promptly."""
    monkeypatch.setenv("HERMES_REMEDY_TIMEOUT_S", "0.2")

    def slow_invoke(payload):
        time.sleep(2.0)  # well past the 0.2s cap
        return "should never appear"

    monkeypatch.setattr(remedy_module, "_invoke_skill", slow_invoke)

    start = time.monotonic()
    with pytest.raises(HTTPException) as ei:
        await create_run(
            RunCreate(dataset="d", base_model="totally/made-up"), db_session
        )
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"timeout cap should keep the user-facing 422 under ~1s; took {elapsed:.2f}s"
    )
    assert ei.value.detail["remedy"] is None  # type: ignore[index]
    assert "should never appear" not in str(ei.value.detail)


@pytest.mark.asyncio
async def test_cache_hit_skips_second_invocation(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Identical (message, context) → second call returns cached remedy without invoking Hermes."""
    calls = {"n": 0}

    def counting_invoke(payload):
        calls["n"] += 1
        return "use the catalog default"

    monkeypatch.setattr(remedy_module, "_invoke_skill", counting_invoke)

    msg = "Base model 'foo/bar' not in catalog"
    ctx = {"endpoint": "POST /api/v1/runs", "base_model": "foo/bar"}

    r1 = await remedy_module.translate_error(msg, ctx)
    r2 = await remedy_module.translate_error(msg, ctx)

    assert r1 == "use the catalog default"
    assert r2 == r1
    assert calls["n"] == 1, "cache hit must short-circuit the second Hermes call"


@pytest.mark.asyncio
async def test_remedy_disabled_via_env_short_circuits(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HERMES_REMEDY_ENABLED", "false")
    called = {"n": 0}

    def counter(payload):
        called["n"] += 1
        return "should not be called"

    monkeypatch.setattr(remedy_module, "_invoke_skill", counter)

    with pytest.raises(HTTPException) as ei:
        await create_run(
            RunCreate(dataset="d", base_model="totally/made-up"), db_session
        )

    assert called["n"] == 0
    assert ei.value.detail["remedy"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_translate_error_returns_none_on_empty_message(monkeypatch: pytest.MonkeyPatch):
    """Empty error → short-circuit before invoking Hermes (no LLM call for nothing)."""
    called = {"n": 0}

    monkeypatch.setattr(
        remedy_module,
        "_invoke_skill",
        lambda payload: (called.__setitem__("n", called["n"] + 1) or "x"),
    )

    out = await remedy_module.translate_error("", {"endpoint": "x"})
    assert out is None
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_existing_422_status_assertion_still_passes(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    """Smoke: existing tests that only check ``status_code == 422`` keep working."""
    monkeypatch.setattr(remedy_module, "_invoke_skill", lambda payload: "anything")

    with pytest.raises(HTTPException) as ei:
        await create_run(
            RunCreate(dataset="d", base_model="totally/made-up"), db_session
        )
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_synth_4xx_carries_remedy(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Sanity-check the synth-router try/except wrapper: 4xx becomes dict-detail."""
    # The 404 raise inside start_synth is the easiest path to trigger — source
    # dataset doesn't exist.
    from apps.api.routers import synth as synth_router

    # Redirect the dataset root so the request resolves to a non-existent path.
    monkeypatch.setattr(synth_router, "DATA_ROOT", tmp_path)

    monkeypatch.setattr(
        remedy_module,
        "_invoke_skill",
        lambda payload: "Create the seed dataset first.",
    )

    req = synth_router.SynthRequest(
        source_dataset="missing-seed",
        new_dataset="output-set",
        target_count=16,
        train_ratio=0.8,
        valid_ratio=0.1,
        canary_ratio=0.1,
    )

    class _FakeRequest:
        state = type("S", (), {"user": type("U", (), {"id": "x", "roles": ["admin"]})()})()

    # Strip the @requires decorator overhead by calling the underlying function
    # — the decorator is exercised in tests/api/test_auth.py and we want focus
    # on the remedy contract here.
    inner = synth_router.start_synth.__wrapped__ if hasattr(synth_router.start_synth, "__wrapped__") else synth_router.start_synth

    with pytest.raises(HTTPException) as ei:
        await inner(req, _FakeRequest())  # type: ignore[arg-type]

    assert ei.value.status_code == 404
    assert isinstance(ei.value.detail, dict)
    assert ei.value.detail["remedy"] == "Create the seed dataset first."
    assert "missing-seed" in ei.value.detail["message"]
