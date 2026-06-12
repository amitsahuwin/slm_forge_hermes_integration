"""Phase O / A1, A2, A3 — MlxBackend parity with the pre-refactor runner.py logic.

These tests pin the *exact* behavior of the old `_write_yaml_config`,
metric-regex block, and `_build_mlx_lora_cmd` so the extraction into
`MlxBackend` is provably behavior-preserving. They are hermetic: no mlx,
no network, no Metal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from packages.trainer.backends.base import TrainEvent
from packages.trainer.backends.mlx import MlxBackend


def _sample_run(**overrides: Any) -> dict:
    run = {
        "id": 1,
        "dataset": "demo",
        "base_model": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "method": "lora",
        "iters": 100,
        "batch_size": 2,
        "learning_rate": 1.0e-4,
        "num_layers": 16,
        "max_seq_length": 1024,
        "grad_checkpoint": False,
        "seed": 0,
    }
    run.update(overrides)
    return run


def _make_dataset(
    tmp_path: Path, *, fmt: str = "chat", n_train: int = 4, n_valid: int = 7
) -> Path:
    ds = tmp_path / "dataset"
    ds.mkdir()
    if fmt == "chat":
        row = {"messages": [{"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "ho"}]}
    elif fmt == "prompt_completion":
        row = {"prompt": "hi", "completion": "ho"}
    else:
        row = {"text": "plain old text row"}
    line = json.dumps(row)
    (ds / "train.jsonl").write_text("\n".join([line] * n_train) + "\n")
    (ds / "valid.jsonl").write_text(
        "\n".join([line] * n_valid) + "\n" if n_valid else ""
    )
    return ds


# ---------------------------------------------------------------------------
# A1 — config parity (golden YAML pinned from pre-refactor _write_yaml_config)
# ---------------------------------------------------------------------------

def test_write_config_golden_chat_dataset(tmp_path: Path) -> None:
    run = _sample_run()
    ds = _make_dataset(tmp_path, fmt="chat", n_valid=7)
    adapter_dir = tmp_path / "runs" / "1" / "adapter"

    cfg_path = MlxBackend().write_config(run, ds, adapter_dir)

    assert cfg_path == tmp_path / "runs" / "1" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg == {
        "model": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "train": True,
        "data": str(ds),
        "fine_tune_type": "lora",
        "num_layers": 16,
        "batch_size": 2,
        "iters": 100,
        "learning_rate": 1.0e-4,
        "val_batches": 3,          # floor(7 / 2) = 3, under the 25 cap
        "steps_per_report": 10,
        "steps_per_eval": 20,      # max(20, 100 // 10)
        "save_every": 50,          # max(50, 100 // 4)
        "adapter_path": str(adapter_dir),
        "max_seq_length": 1024,
        "grad_checkpoint": False,
        "seed": 0,
        "mask_prompt": True,       # chat dataset → mask prompt tokens
    }


def test_write_config_text_dataset_disables_mask_prompt(tmp_path: Path) -> None:
    run = _sample_run()
    ds = _make_dataset(tmp_path, fmt="text")
    cfg_path = MlxBackend().write_config(run, ds, tmp_path / "r" / "adapter")
    cfg = yaml.safe_load(cfg_path.read_text())
    # MLX-LM hard-errors on mask_prompt=True for text datasets.
    assert cfg["mask_prompt"] is False


def test_write_config_prompt_completion_counts_as_chat(tmp_path: Path) -> None:
    run = _sample_run()
    ds = _make_dataset(tmp_path, fmt="prompt_completion")
    cfg_path = MlxBackend().write_config(run, ds, tmp_path / "r" / "adapter")
    assert yaml.safe_load(cfg_path.read_text())["mask_prompt"] is True


def test_write_config_val_batches_zero_when_no_valid_rows(tmp_path: Path) -> None:
    run = _sample_run()
    ds = _make_dataset(tmp_path, fmt="chat", n_valid=0)
    cfg_path = MlxBackend().write_config(run, ds, tmp_path / "r" / "adapter")
    assert yaml.safe_load(cfg_path.read_text())["val_batches"] == 0


def test_write_config_val_batches_capped_at_25(tmp_path: Path) -> None:
    run = _sample_run(batch_size=4)
    ds = _make_dataset(tmp_path, fmt="chat", n_valid=200)  # floor(200/4)=50
    cfg_path = MlxBackend().write_config(run, ds, tmp_path / "r" / "adapter")
    assert yaml.safe_load(cfg_path.read_text())["val_batches"] == 25


def test_write_config_long_runs_scale_eval_and_save(tmp_path: Path) -> None:
    run = _sample_run(iters=1000)
    ds = _make_dataset(tmp_path, fmt="chat")
    cfg = yaml.safe_load(
        MlxBackend().write_config(run, ds, tmp_path / "r" / "adapter").read_text()
    )
    assert cfg["steps_per_eval"] == 100   # max(20, 1000 // 10)
    assert cfg["save_every"] == 250       # max(50, 1000 // 4)


# ---------------------------------------------------------------------------
# A2 — parse parity (real mlx-lm stdout lines → normalized TrainEvents)
# ---------------------------------------------------------------------------

def test_parse_line_train_iteration_yields_four_events() -> None:
    line = ("Iter 10: Train loss 2.345, Learning Rate 1.000e-04, "
            "It/sec 1.230, Tokens/sec 456.700")
    events = MlxBackend().parse_line(line)
    assert events == [
        TrainEvent(step=10, name="train_loss", value=2.345),
        TrainEvent(step=10, name="learning_rate", value=1.0e-4),
        TrainEvent(step=10, name="iters_per_sec", value=1.23),
        TrainEvent(step=10, name="tokens_per_sec", value=456.7),
    ]


def test_parse_line_val_iteration() -> None:
    events = MlxBackend().parse_line("Iter 20: Val loss 2.100, Val took 5.012s")
    assert events == [TrainEvent(step=20, name="val_loss", value=2.1)]


@pytest.mark.parametrize("line", [
    "Loading pretrained model",
    "Starting training..., iters: 100",
    "Iter 50: Saved adapter weights",
    "",
])
def test_parse_line_noise_yields_nothing(line: str) -> None:
    assert MlxBackend().parse_line(line) == []


# ---------------------------------------------------------------------------
# A3 — command discovery parity (subprocess monkeypatched, no mlx needed)
# ---------------------------------------------------------------------------

def _fake_run_factory(ok_argv_prefix: list[str] | None):
    """subprocess.run stub: succeed only for the given `python -m ...` prefix."""
    def fake_run(argv, **kwargs):
        rc = 0 if (ok_argv_prefix is not None and argv[:3] == ok_argv_prefix) else 1
        return SimpleNamespace(returncode=rc, stdout="", stderr="")
    return fake_run


def test_build_command_prefers_modern_subcommand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import packages.trainer.backends.mlx as mlx_mod

    monkeypatch.setattr(
        mlx_mod.subprocess, "run",
        _fake_run_factory([sys.executable, "-m", "mlx_lm"]),
    )
    cfg = tmp_path / "config.yaml"
    cmd = MlxBackend().build_command(cfg)
    assert cmd == [sys.executable, "-m", "mlx_lm", "lora", "--config", str(cfg)]


def test_build_command_falls_back_to_legacy_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import packages.trainer.backends.mlx as mlx_mod

    monkeypatch.setattr(
        mlx_mod.subprocess, "run",
        _fake_run_factory([sys.executable, "-m", "mlx_lm.lora"]),
    )
    cfg = tmp_path / "config.yaml"
    cmd = MlxBackend().build_command(cfg)
    assert cmd == [sys.executable, "-m", "mlx_lm.lora", "--config", str(cfg)]


def test_build_command_returns_none_when_toolchain_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import packages.trainer.backends.mlx as mlx_mod

    monkeypatch.setattr(mlx_mod.subprocess, "run", _fake_run_factory(None))
    # Point the scripts dir somewhere empty and erase PATH lookups.
    monkeypatch.setattr(mlx_mod.sysconfig, "get_path", lambda _key: str(tmp_path))
    monkeypatch.setattr(mlx_mod.shutil, "which", lambda _name: None)
    assert MlxBackend().build_command(tmp_path / "config.yaml") is None


def test_missing_toolchain_message_mentions_mlx_lm() -> None:
    msg = MlxBackend().missing_toolchain_message()
    assert "mlx_lm" in msg
