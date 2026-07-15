"""Phase U — the ratchet loop propagates the session's trainer_backend into
every Run it queues. Without this, autoresearch runs default to 'mlx' and a
CUDA-only host never claims them."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

import packages.ratchet.loop as loop


class FakeAPI:
    """Records create_run payloads; returns an immediately-completed run."""

    def __init__(self, session: dict) -> None:
        self.base = "http://test"
        self.session = session
        self.create_payloads: list[dict] = []
        self._runs: dict[int, dict] = {}
        self._next_id = 1

    def get_session(self, sid: int) -> dict:
        return self.session

    def patch_session(self, sid: int, **fields: Any) -> None:
        self.session.update(fields)

    def list_iterations(self, sid: int) -> list[dict]:
        return list(self._runs.values())

    def create_run(self, payload: dict) -> dict:
        self.create_payloads.append(payload)
        rid = self._next_id
        self._next_id += 1
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


class _NoOpResponse:
    def raise_for_status(self) -> _NoOpResponse:
        return self


class _NoOpHttpx:
    """Swallow the direct httpx.patch/post bookkeeping calls in run_session."""

    # run_session catches httpx.HTTPError around these calls.
    HTTPError = httpx.HTTPError

    @staticmethod
    def patch(*a: Any, **k: Any) -> _NoOpResponse:
        return _NoOpResponse()

    @staticmethod
    def post(*a: Any, **k: Any) -> _NoOpResponse:
        return _NoOpResponse()


def _session(backend: str) -> dict:
    return {
        "id": 1,
        "name": "exp",
        "dataset": "demo",
        "base_model": "Qwen/Qwen2.5-3B-Instruct",
        "method": "lora",
        "iters": 50,
        "batch_size": 4,
        "learning_rate": 1e-4,
        "num_layers": 16,
        "max_seq_length": 2048,
        "max_rounds": 1,  # single baseline round → no Hermes mutation call
        "plateau_patience": 3,
        "min_delta": 0.005,
        "canary_drift_threshold": 0.3,
        "trainer_backend": backend,
    }


@pytest.mark.parametrize("backend", ["cuda", "mlx"])
def test_run_payload_carries_session_backend(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop, "httpx", _NoOpHttpx)
    api = FakeAPI(_session(backend))

    loop.run_session(1, api)

    assert api.create_payloads, "no run was created"
    for payload in api.create_payloads:
        assert payload["trainer_backend"] == backend


def test_run_payload_defaults_backend_for_legacy_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session dict from before Phase U (no trainer_backend key) → 'mlx'."""
    monkeypatch.setattr(loop, "httpx", _NoOpHttpx)
    sess = _session("cuda")
    del sess["trainer_backend"]
    api = FakeAPI(sess)

    loop.run_session(1, api)

    assert api.create_payloads
    assert api.create_payloads[0]["trainer_backend"] == "mlx"


def test_run_payload_carries_session_grad_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session's grad_checkpoint choice is threaded onto every child run."""
    monkeypatch.setattr(loop, "httpx", _NoOpHttpx)
    sess = _session("mlx")
    sess["grad_checkpoint"] = False
    api = FakeAPI(sess)

    loop.run_session(1, api)

    assert api.create_payloads
    assert api.create_payloads[0]["grad_checkpoint"] is False


def test_run_payload_defaults_grad_checkpoint_on_for_legacy_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session row from before the column existed → memory-safe True."""
    monkeypatch.setattr(loop, "httpx", _NoOpHttpx)
    api = FakeAPI(_session("mlx"))  # no grad_checkpoint key

    loop.run_session(1, api)

    assert api.create_payloads
    assert api.create_payloads[0]["grad_checkpoint"] is True
