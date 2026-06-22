"""PR-2 — post-mortem service generates markdown, persists DB + sidecar, dedupes.

These tests run the service directly (no HTTP). The HTTP wiring is covered
by ``test_run_post_mortem.py``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.run import Run, RunStatus
from apps.api.models.session import TrainingSession  # FK target for Run.session_id
from apps.api.services import db as db_module
from apps.api.services import post_mortem as pm_module


@pytest.fixture()
def isolated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite with the Run + TrainingSession tables (FK target for Run.session_id)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'pm.db'}")
    SQLModel.metadata.create_all(
        eng,
        tables=[TrainingSession.__table__, Run.__table__],  # type: ignore[arg-type]
    )
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


@pytest.fixture()
def artifacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ARTIFACTS_ROOT so sidecar writes go to a tmp dir."""
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(pm_module, "ARTIFACTS_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test gets fresh semaphore + lock state."""
    pm_module._semaphore = None
    pm_module._locks.clear()
    yield
    pm_module._semaphore = None
    pm_module._locks.clear()


def _seed_failed_run(eng, **overrides: Any) -> int:
    with Session(eng) as s:
        run = Run(
            dataset="d",
            base_model="m",
            status=RunStatus.FAILED,
            error_message="boom",
            **overrides,
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        assert run.id is not None
        return run.id


@pytest.mark.asyncio
async def test_happy_path_persists_markdown_and_sidecar(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """Skill returns markdown → DB row + sidecar file are written."""
    run_id = _seed_failed_run(isolated_engine)
    markdown = "# Post-mortem\n\nRoot cause: dataset shape mismatch."

    # ``run_skill`` is what the service calls under asyncio.to_thread.
    monkeypatch.setattr(
        "packages.ratchet.hermes_bridge.run_skill",
        lambda name, payload, expect_json=False: markdown,
    )

    await pm_module.generate_for_run(run_id)

    with Session(isolated_engine) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.post_mortem == markdown
        assert run.post_mortem_status == "ready"
        assert run.post_mortem_input_hash is not None
        assert run.post_mortem_generated_at is not None

    sidecar = artifacts_root / str(run_id) / "post_mortem.md"
    assert sidecar.exists(), "sidecar markdown should be written for artifact bundles"
    assert sidecar.read_text() == markdown


@pytest.mark.asyncio
async def test_skill_uses_failure_post_mortem_name(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """Service must invoke the ``failure_post_mortem`` skill (not e.g. propose_mutation)."""
    run_id = _seed_failed_run(isolated_engine)
    called: dict[str, Any] = {}

    def fake_run_skill(name, payload, expect_json=False):
        called["name"] = name
        called["expect_json"] = expect_json
        called["payload_run_id"] = payload.get("run_id")
        return "md"

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", fake_run_skill)

    await pm_module.generate_for_run(run_id)

    assert called["name"] == "failure_post_mortem"
    assert called["expect_json"] is False  # markdown, not JSON
    assert called["payload_run_id"] == run_id


@pytest.mark.asyncio
async def test_ollama_down_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """Hermes blowing up does NOT propagate — status flips to ``unavailable``."""
    import httpx

    run_id = _seed_failed_run(isolated_engine)

    def explode(name, payload, expect_json=False):
        raise httpx.ConnectError("ollama down")

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", explode)

    # MUST NOT raise.
    await pm_module.generate_for_run(run_id)

    with Session(isolated_engine) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.post_mortem_status == "unavailable"
        assert run.post_mortem is not None
        assert "unavailable" in run.post_mortem.lower()


@pytest.mark.asyncio
async def test_skill_not_installed_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    run_id = _seed_failed_run(isolated_engine)

    def missing(name, payload, expect_json=False):
        raise FileNotFoundError(f"skill {name!r} not found")

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", missing)

    await pm_module.generate_for_run(run_id)

    with Session(isolated_engine) as s:
        run = s.get(Run, run_id)
        assert run is not None
        assert run.post_mortem_status == "unavailable"


@pytest.mark.asyncio
async def test_cache_hit_skips_second_skill_call(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """Same fingerprint twice → only one Hermes call (cache key dedupes)."""
    run_id = _seed_failed_run(isolated_engine)
    calls = {"n": 0}

    def counter(name, payload, expect_json=False):
        calls["n"] += 1
        return "# md"

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", counter)

    await pm_module.generate_for_run(run_id)
    assert calls["n"] == 1

    # Second invocation with the same DB state (same error_message + log_tail)
    # must be a cache hit — no second call to the skill.
    await pm_module.generate_for_run(run_id)
    assert calls["n"] == 1, "cache hit must short-circuit the second skill call"


@pytest.mark.asyncio
async def test_disabled_via_env_short_circuits(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    run_id = _seed_failed_run(isolated_engine)
    monkeypatch.setenv("HERMES_POST_MORTEM_ENABLED", "false")
    called = {"n": 0}

    def counter(name, payload, expect_json=False):
        called["n"] += 1
        return "md"

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", counter)

    await pm_module.generate_for_run(run_id)

    assert called["n"] == 0, "feature flag off should skip skill invocation"

    with Session(isolated_engine) as s:
        run = s.get(Run, run_id)
        # Status was set to ``"skipped"`` by the Run model default; the
        # service short-circuit must not flip it elsewhere.
        assert run.post_mortem_status == "skipped"


@pytest.mark.asyncio
async def test_skips_when_run_no_longer_failed(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """If status was flipped to COMPLETED before the background task fired,
    the service must NOT generate a post-mortem."""
    run_id = _seed_failed_run(isolated_engine)

    # Flip back to COMPLETED — simulating a fast operator retry.
    with Session(isolated_engine) as s:
        run = s.get(Run, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        s.add(run)
        s.commit()

    called = {"n": 0}

    def counter(name, payload, expect_json=False):
        called["n"] += 1
        return "md"

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", counter)

    await pm_module.generate_for_run(run_id)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_tail_log_reads_sidecar_log(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """``_tail_log`` reads ``runs/<id>/training.log`` if present."""
    run_id = _seed_failed_run(isolated_engine)
    log_dir = artifacts_root / str(run_id)
    log_dir.mkdir()
    log_lines = "\n".join(f"line {i}" for i in range(50))
    (log_dir / "training.log").write_text(log_lines)

    tail = pm_module._tail_log(run_id, lines=5)
    assert "line 49" in tail
    assert "line 45" in tail
    assert "line 0" not in tail


@pytest.mark.asyncio
async def test_input_hash_changes_with_error_message():
    h1 = pm_module._input_hash("oom", "log line")
    h2 = pm_module._input_hash("oom", "log line")
    h3 = pm_module._input_hash("different", "log line")
    assert h1 == h2
    assert h1 != h3


@pytest.mark.asyncio
async def test_per_run_lock_serializes_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch, isolated_engine, artifacts_root
):
    """Two concurrent ``generate_for_run`` invocations for the same id must
    serialize on the per-run lock and produce exactly ONE skill call."""
    run_id = _seed_failed_run(isolated_engine)
    calls = {"n": 0}
    started = asyncio.Event()
    proceed = asyncio.Event()

    def slow_skill(name, payload, expect_json=False):
        calls["n"] += 1
        started.set()
        # Synchronous block — but the service offloads via asyncio.to_thread,
        # so the second coroutine can reach the lock and wait there.

        # Give the second invocation time to queue up on the lock.
        proceed.wait(timeout=2.0)
        return "md"

    monkeypatch.setattr("packages.ratchet.hermes_bridge.run_skill", slow_skill)

    task1 = asyncio.create_task(pm_module.generate_for_run(run_id))
    await started.wait()
    task2 = asyncio.create_task(pm_module.generate_for_run(run_id))
    # let task1 finish — task2 will then enter the lock, find a cache hit, and return.
    proceed.set()
    await asyncio.gather(task1, task2)

    assert calls["n"] == 1, "per-run lock + cache hit must collapse concurrent calls"
