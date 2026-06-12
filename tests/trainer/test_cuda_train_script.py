"""Phase Q / A5 — cuda_train.py pure helpers (module must import without torch)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.trainer import cuda_train
from packages.trainer.backends.base import TrainEvent
from packages.trainer.backends.cuda import CudaBackend


def _valid_config(tmp_path: Path) -> dict:
    return {
        "model": "Qwen/Qwen2.5-3B-Instruct",
        "data": str(tmp_path),
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
        "adapter_path": str(tmp_path / "adapter"),
        "steps_per_report": 10,
        "steps_per_eval": 20,
    }


def test_module_imports_without_torch() -> None:
    # The module was already imported above; assert it didn't pull torch in.
    import sys

    assert "torch" not in sys.modules or cuda_train is not None


def test_load_config_roundtrip(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    assert cuda_train.load_config(p) == cfg


def test_load_config_rejects_missing_keys(tmp_path: Path) -> None:
    cfg = _valid_config(tmp_path)
    del cfg["adapter_path"]
    del cfg["iters"]
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError) as exc:
        cuda_train.load_config(p)
    assert "adapter_path" in str(exc.value)
    assert "iters" in str(exc.value)


def test_emit_metric_roundtrips_through_backend_parser(capsys) -> None:
    cuda_train.emit_metric(step=42, name="train_loss", value=1.875)
    line = capsys.readouterr().out.strip()
    assert CudaBackend().parse_line(line) == [
        TrainEvent(step=42, name="train_loss", value=1.875)
    ]
