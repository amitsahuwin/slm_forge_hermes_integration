"""MCP sync — server tools stay in lockstep with the Phases O-S API surface."""
from __future__ import annotations

from typing import Any

import pytest

mcp_server = pytest.importorskip(
    "mcp_server.server", reason="mcp extra not installed"
)

from apps.api.services.model_catalog import default_model_id  # noqa: E402


@pytest.fixture()
def recorded(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append(("GET", path, params))
        return []

    def fake_post(path: str, json: dict[str, Any] | None = None) -> Any:
        calls.append(("POST", path, json))
        return {"id": 1, **(json or {})}

    monkeypatch.setattr(mcp_server, "_get", fake_get)
    monkeypatch.setattr(mcp_server, "_post", fake_post)
    return calls


# A1 — the experiment default can never point at a broken/stale model again.
def test_start_experiment_default_matches_catalog() -> None:
    import inspect

    sig = inspect.signature(mcp_server.start_experiment)
    assert sig.parameters["base_model"].default == default_model_id("mlx")


# A2 — catalog v2 exposure.
def test_list_models_hits_v2(recorded) -> None:
    mcp_server.list_models()
    assert recorded == [("GET", "/api/v1/models/v2", None)]


# A3 — single-run creation with backend routing.
def test_start_run_posts_trainer_backend_default_mlx(recorded) -> None:
    mcp_server.start_run(dataset="demo")
    method, path, body = recorded[0]
    assert (method, path) == ("POST", "/api/v1/runs")
    assert body["trainer_backend"] == "mlx"
    assert body["base_model"] == default_model_id("mlx")


def test_start_run_passes_cuda_backend(recorded) -> None:
    mcp_server.start_run(
        dataset="demo",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        trainer_backend="cuda",
    )
    _m, _p, body = recorded[0]
    assert body["trainer_backend"] == "cuda"
    assert body["base_model"] == "Qwen/Qwen2.5-3B-Instruct"


# A4 — backend filter forwarding.
def test_list_runs_forwards_backend_filter(recorded) -> None:
    mcp_server.list_runs(backend="cuda")
    _m, _p, params = recorded[0]
    assert params["backend"] == "cuda"


def test_list_runs_omits_backend_when_unset(recorded) -> None:
    mcp_server.list_runs()
    _m, _p, params = recorded[0]
    assert "backend" not in params
