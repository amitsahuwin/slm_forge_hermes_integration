"""PR-1 A2 — ratchet ``run_session`` handles ``MutationProposalError`` correctly.

  - One failed proposal → iteration skipped, loop continues.
  - N consecutive failures → session marked failed, loop returns early.
  - Successful proposal resets the failure streak.
"""
from __future__ import annotations

from typing import Any

import pytest

import packages.ratchet.loop as loop
from packages.ratchet.hermes_bridge import MutationProposal, MutationProposalError


class _FakeAPI:
    def __init__(self, session: dict) -> None:
        self.base = "http://test"
        self.session = session
        self.create_payloads: list[dict] = []
        self.session_patches: list[dict] = []
        self._next = 1
        self._runs: dict[int, dict] = {}

    def get_session(self, sid: int) -> dict:
        return self.session

    def patch_session(self, sid: int, **fields: Any) -> None:
        self.session_patches.append(fields)
        self.session.update(fields)

    def list_iterations(self, sid: int) -> list[dict]:
        return list(self._runs.values())

    def create_run(self, payload: dict) -> dict:
        self.create_payloads.append(payload)
        rid = self._next
        self._next += 1
        run = {
            "id": rid,
            "status": "completed",
            "final_val_loss": 0.5,
            "canary_loss": None,
            **payload,
        }
        self._runs[rid] = run
        return run

    def get_run(self, rid: int) -> dict:
        return self._runs[rid]

    def patch_run(self, rid: int, **fields: Any) -> None:
        self._runs[rid].update(fields)


def _session(**overrides: Any) -> dict:
    base = {
        "id": 1,
        "name": "t",
        "dataset": "d",
        "base_model": "m",
        "method": "lora",
        "iters": 200,
        "batch_size": 4,
        "learning_rate": 1e-4,
        "num_layers": 8,
        "max_seq_length": 512,
        "max_rounds": 4,
        "plateau_patience": 99,
        "min_delta": 0.0,
        "canary_drift_threshold": 0.1,
        "trainer_backend": "mlx",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _stub_inline_httpx(monkeypatch: pytest.MonkeyPatch):
    """``run_session`` calls httpx.patch / httpx.post directly for some hops.
    Stub these so the loop runs without a real API server."""
    import httpx as _httpx

    class _Resp:
        def raise_for_status(self) -> None: ...

    monkeypatch.setattr(_httpx, "patch", lambda *a, **kw: _Resp())
    monkeypatch.setattr(_httpx, "post", lambda *a, **kw: _Resp())


def test_one_proposal_failure_skips_iteration_and_continues(monkeypatch: pytest.MonkeyPatch):
    api = _FakeAPI(_session(max_rounds=3))
    calls = {"n": 0}

    def propose(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MutationProposalError("bad json")
        return MutationProposal(learning_rate=5e-5, reasoning="ok", expected_outcome="ok")

    monkeypatch.setattr(loop, "propose_mutation", propose)

    loop.run_session(1, api)  # type: ignore[arg-type]

    # Round 0 = baseline (1 create_run). Round 1 = proposal fail → skipped (no create_run).
    # Round 2 = proposal ok (1 create_run). Total: 2 runs created.
    assert len(api.create_payloads) == 2
    # Final session status reached "completed", NOT "failed".
    statuses = [p.get("status") for p in api.session_patches if "status" in p]
    assert "failed" not in statuses
    assert statuses[-1] == "completed"


def test_session_aborts_after_n_consecutive_failures(monkeypatch: pytest.MonkeyPatch):
    api = _FakeAPI(_session(max_rounds=10))
    monkeypatch.setenv("HERMES_MAX_PROPOSAL_FAILURES", "2")

    def propose(**kwargs):
        raise MutationProposalError("always bad")

    monkeypatch.setattr(loop, "propose_mutation", propose)

    loop.run_session(1, api)  # type: ignore[arg-type]

    # Baseline iteration ran (1 create_run). Then 2 consecutive failures → abort.
    assert len(api.create_payloads) == 1
    statuses = [p.get("status") for p in api.session_patches if "status" in p]
    assert "failed" in statuses
    # ``completed`` must NOT also appear.
    assert "completed" not in statuses


def test_successful_proposal_resets_failure_streak(monkeypatch: pytest.MonkeyPatch):
    api = _FakeAPI(_session(max_rounds=5))
    monkeypatch.setenv("HERMES_MAX_PROPOSAL_FAILURES", "2")

    sequence = iter(
        [
            MutationProposalError("e1"),  # round 1: fail
            MutationProposal(learning_rate=5e-5, reasoning="r"),  # round 2: ok → resets streak
            MutationProposalError("e2"),  # round 3: fail (streak=1, below threshold)
            MutationProposal(learning_rate=5e-5, reasoning="r"),  # round 4: ok
        ]
    )

    def propose(**kwargs):
        item = next(sequence)
        if isinstance(item, MutationProposalError):
            raise item
        return item

    monkeypatch.setattr(loop, "propose_mutation", propose)

    loop.run_session(1, api)  # type: ignore[arg-type]

    statuses = [p.get("status") for p in api.session_patches if "status" in p]
    assert "failed" not in statuses, "streak must reset after success — session should not abort"
