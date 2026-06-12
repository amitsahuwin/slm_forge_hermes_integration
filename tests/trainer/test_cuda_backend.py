"""Phase Q / A1-A4 — CudaBackend: registry, config, parsing, command probe."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.trainer.backends import ENV_VAR, get_backend
from packages.trainer.backends.base import TrainerBackend, TrainEvent
from packages.trainer.backends.cuda import CudaBackend


def _sample_run(**overrides):
    run = {
        "id": 5,
        "dataset": "demo",
        "base_model": "Qwen/Qwen2.5-3B-Instruct",
        "method": "lora",
        "iters": 200,
        "batch_size": 4,
        "learning_rate": 1.0e-4,
        "num_layers": 16,
        "max_seq_length": 2048,
        "grad_checkpoint": False,
        "seed": 0,
    }
    run.update(overrides)
    return run


def _make_dataset(tmp_path: Path, fmt: str = "chat") -> Path:
    ds = tmp_path / "ds"
    ds.mkdir()
    row = (
        {"messages": [{"role": "user", "content": "q"},
                      {"role": "assistant", "content": "a"}]}
        if fmt == "chat" else {"text": "plain row"}
    )
    line = json.dumps(row) + "\n"
    (ds / "train.jsonl").write_text(line * 8)
    (ds / "valid.jsonl").write_text(line * 2)
    return ds


# ---------------------------------------------------------------------------
# A1 — registry
# ---------------------------------------------------------------------------

def test_cuda_backend_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = get_backend("cuda")
    assert isinstance(backend, CudaBackend)
    assert isinstance(backend, TrainerBackend)
    assert backend.name == "cuda"

    monkeypatch.setenv(ENV_VAR, "cuda")
    assert isinstance(get_backend(), CudaBackend)


def test_unknown_backend_error_lists_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError) as exc:
        get_backend("tpu")
    assert "cuda" in str(exc.value)
    assert "mlx" in str(exc.value)


# ---------------------------------------------------------------------------
# A2 — config.json golden
# ---------------------------------------------------------------------------

def test_write_config_golden_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_CUDA_QUANT", raising=False)
    ds = _make_dataset(tmp_path, "chat")
    adapter_dir = tmp_path / "runs" / "5" / "adapter"

    cfg_path = CudaBackend().write_config(_sample_run(), ds, adapter_dir)

    assert cfg_path == tmp_path / "runs" / "5" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    assert cfg == {
        "model": "Qwen/Qwen2.5-3B-Instruct",
        "data": str(ds),
        "dataset_format": "chat",
        "mask_prompt": True,
        "fine_tune_type": "lora",
        "lora_rank": 16,
        "lora_alpha": 32,
        "batch_size": 4,
        "iters": 200,
        "learning_rate": 1.0e-4,
        "max_seq_length": 2048,
        "grad_checkpoint": False,
        "seed": 0,
        "quant": "nf4",
        "adapter_path": str(adapter_dir),
        "steps_per_report": 10,
        "steps_per_eval": 20,
    }


def test_write_config_text_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_CUDA_QUANT", raising=False)
    ds = _make_dataset(tmp_path, "text")
    cfg_path = CudaBackend().write_config(_sample_run(), ds, tmp_path / "r" / "adapter")
    cfg = json.loads(cfg_path.read_text())
    assert cfg["dataset_format"] == "text"
    assert cfg["mask_prompt"] is False


def test_write_config_quant_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLM_FORGE_CUDA_QUANT", "none")
    ds = _make_dataset(tmp_path, "chat")
    cfg_path = CudaBackend().write_config(_sample_run(), ds, tmp_path / "r" / "adapter")
    assert json.loads(cfg_path.read_text())["quant"] == "none"


# ---------------------------------------------------------------------------
# A3 — JSONL parsing (never raises, ignores noise)
# ---------------------------------------------------------------------------

def test_parse_line_metric() -> None:
    line = json.dumps({"event": "metric", "step": 10, "name": "train_loss", "value": 2.5})
    assert CudaBackend().parse_line(line) == [
        TrainEvent(step=10, name="train_loss", value=2.5)
    ]


@pytest.mark.parametrize("line", [
    "",
    "100%|██████████| 200/200 [01:00<00:00]",          # tqdm
    "Some HF warning about pad_token",
    json.dumps({"event": "info", "message": "starting"}),  # non-metric event
    json.dumps({"step": 10, "name": "x", "value": 1.0}),   # missing event key
    '{"event": "metric", "step": ',                         # malformed JSON
    json.dumps({"event": "metric", "step": "ten", "name": "x", "value": 1.0}),
])
def test_parse_line_noise_is_ignored(line: str) -> None:
    assert CudaBackend().parse_line(line) == []


# ---------------------------------------------------------------------------
# A4 — command probe
# ---------------------------------------------------------------------------

def test_build_command_when_toolchain_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import packages.trainer.backends.cuda as cuda_mod

    monkeypatch.setattr(
        cuda_mod.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    cfg = tmp_path / "config.json"
    cmd = CudaBackend().build_command(cfg)
    assert cmd == [sys.executable, "-m", "packages.trainer.cuda_train", "--config", str(cfg)]


def test_build_command_when_toolchain_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import packages.trainer.backends.cuda as cuda_mod

    monkeypatch.setattr(
        cuda_mod.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="no torch"),
    )
    assert CudaBackend().build_command(tmp_path / "config.json") is None


def test_missing_toolchain_message_mentions_extras() -> None:
    msg = CudaBackend().missing_toolchain_message()
    assert "trainer-cuda" in msg
