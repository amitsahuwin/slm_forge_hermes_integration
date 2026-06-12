"""Phase O / A5 — run_training_job drives any TrainerBackend identically.

Uses a fake backend + fake subprocess.Popen + recorded HTTP helpers to pin
the orchestration contract: status transitions, metric POSTs, final Run
patch, and training.log on disk — with zero MLX involvement.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import packages.trainer.runner as runner
from packages.trainer.backends.base import TrainerBackend, TrainEvent


class FakeBackend(TrainerBackend):
    """Emits one fake metric line format: 'METRIC <step> <name> <value>'."""

    name = "fake"

    def __init__(self, *, toolchain_ok: bool = True) -> None:
        self.toolchain_ok = toolchain_ok

    def write_config(self, run: dict, dataset_dir: Path, adapter_dir: Path) -> Path:
        adapter_dir.parent.mkdir(parents=True, exist_ok=True)
        cfg = adapter_dir.parent / "config.json"
        cfg.write_text(json.dumps({"model": run["base_model"]}))
        return cfg

    def build_command(self, config_path: Path) -> list[str] | None:
        if not self.toolchain_ok:
            return None
        return ["fake-trainer", "--config", str(config_path)]

    def parse_line(self, line: str) -> list[TrainEvent]:
        if not line.startswith("METRIC "):
            return []
        _tag, step, name, value = line.split()
        return [TrainEvent(step=int(step), name=name, value=float(value))]

    def missing_toolchain_message(self) -> str:
        return "fake toolchain missing"


class FakeProc:
    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = iter(line + "\n" for line in lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect runner's filesystem roots + record its HTTP side effects."""
    data_root = tmp_path / "datasets"
    runs_root = tmp_path / "runs"
    ds = data_root / "demo"
    ds.mkdir(parents=True)
    (ds / "train.jsonl").write_text('{"text": "row"}\n')
    (ds / "valid.jsonl").write_text('{"text": "row"}\n')

    monkeypatch.setattr(runner, "DATA_ROOT", data_root)
    monkeypatch.setattr(runner, "RUNS_ROOT", runs_root)

    patches: list[dict[str, Any]] = []
    metrics: list[tuple[int, str, float]] = []
    monkeypatch.setattr(
        runner, "_patch_run",
        lambda api_url, run_id, **fields: patches.append(fields),
    )
    monkeypatch.setattr(
        runner, "_post_metric",
        lambda api_url, run_id, step, name, value: metrics.append((step, name, value)),
    )
    return SimpleSandbox(tmp_path, runs_root, patches, metrics)


class SimpleSandbox:
    def __init__(self, tmp_path, runs_root, patches, metrics):
        self.tmp_path = tmp_path
        self.runs_root = runs_root
        self.patches = patches
        self.metrics = metrics


def _run_dict() -> dict:
    return {
        "id": 7,
        "dataset": "demo",
        "base_model": "any/model",
        "method": "lora",
        "iters": 100,
        "batch_size": 2,
        "learning_rate": 1.0e-4,
        "num_layers": 16,
        "max_seq_length": 512,
        "grad_checkpoint": False,
        "seed": 0,
        "trainer_backend": "fake",
    }


def test_successful_run_lifecycle(sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        "starting up",
        "METRIC 10 train_loss 2.5",
        "METRIC 10 learning_rate 0.0001",
        "METRIC 20 val_loss 2.2",
        "METRIC 30 train_loss 2.1",
        "done",
    ]
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *a, **kw: FakeProc(lines, returncode=0)
    )

    runner.run_training_job(_run_dict(), api_url="http://test", backend=FakeBackend())

    # Status transitions: running first, completed last.
    assert sandbox.patches[0] == {"status": "running"}
    final = sandbox.patches[-1]
    assert final["status"] == "completed"
    assert final["final_train_loss"] == 2.1   # last train_loss event wins
    assert final["final_val_loss"] == 2.2
    assert final["adapter_path"] == str(sandbox.runs_root / "7" / "adapter")

    # Metric stream preserved in order.
    assert sandbox.metrics == [
        (10, "train_loss", 2.5),
        (10, "learning_rate", 0.0001),
        (20, "val_loss", 2.2),
        (30, "train_loss", 2.1),
    ]

    # Raw stdout persisted to training.log (parity with old behavior).
    log_text = (sandbox.runs_root / "7" / "training.log").read_text()
    assert "starting up" in log_text and "done" in log_text


def test_failed_subprocess_marks_run_failed(
    sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner.subprocess, "Popen",
        lambda *a, **kw: FakeProc(["boom"], returncode=3),
    )
    runner.run_training_job(_run_dict(), api_url="http://test", backend=FakeBackend())
    final = sandbox.patches[-1]
    assert final["status"] == "failed"
    assert "3" in final["error_message"]


def test_missing_toolchain_marks_run_failed(sandbox) -> None:
    runner.run_training_job(
        _run_dict(), api_url="http://test", backend=FakeBackend(toolchain_ok=False)
    )
    final = sandbox.patches[-1]
    assert final["status"] == "failed"
    assert "fake toolchain missing" in final["error_message"]


def test_missing_dataset_marks_run_failed(sandbox) -> None:
    run = _run_dict() | {"dataset": "nope"}
    runner.run_training_job(run, api_url="http://test", backend=FakeBackend())
    final = sandbox.patches[-1]
    assert final["status"] == "failed"
    assert "train.jsonl" in final["error_message"]


def test_canary_loss_recorded_when_backend_provides_it(
    sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CanaryBackend(FakeBackend):
        def run_canary_eval(self, run, dataset_dir, adapter_dir, run_dir, env):
            return 1.234

    monkeypatch.setattr(
        runner.subprocess, "Popen",
        lambda *a, **kw: FakeProc(["METRIC 10 train_loss 2.0"], returncode=0),
    )
    runner.run_training_job(_run_dict(), api_url="http://test", backend=CanaryBackend())
    final = sandbox.patches[-1]
    assert final["canary_loss"] == 1.234
    assert (100, "canary_loss", 1.234) in sandbox.metrics
