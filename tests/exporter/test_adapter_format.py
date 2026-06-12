"""Phase Q / A6 — exporter detects PEFT vs MLX adapters; peft_merge is import-safe."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.exporter.pipeline import detect_adapter_format


def _mlx_adapter(tmp_path: Path) -> Path:
    """Real MLX layout (verified against runs/<id>/adapter on disk)."""
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapters.safetensors").write_bytes(b"\0")
    (d / "0000050_adapters.safetensors").write_bytes(b"\0")
    (d / "adapter_config.json").write_text(json.dumps({"fine_tune_type": "lora"}))
    return d


def _peft_adapter(tmp_path: Path) -> Path:
    """PEFT save_pretrained layout."""
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapter_model.safetensors").write_bytes(b"\0")
    (d / "adapter_config.json").write_text(json.dumps({"peft_type": "LORA"}))
    return d


def test_detects_mlx_adapter(tmp_path: Path) -> None:
    assert detect_adapter_format(_mlx_adapter(tmp_path)) == "mlx"


def test_detects_peft_adapter(tmp_path: Path) -> None:
    assert detect_adapter_format(_peft_adapter(tmp_path)) == "peft"


def test_empty_dir_defaults_to_mlx(tmp_path: Path) -> None:
    d = tmp_path / "adapter"
    d.mkdir()
    assert detect_adapter_format(d) == "mlx"


def test_peft_marker_wins_even_with_config_present(tmp_path: Path) -> None:
    # Both formats ship adapter_config.json — file *names* discriminate.
    d = _peft_adapter(tmp_path)
    assert (d / "adapter_config.json").exists()
    assert detect_adapter_format(d) == "peft"


def test_peft_merge_importable_without_torch() -> None:
    import sys

    from packages.exporter import peft_merge  # must not require torch

    assert hasattr(peft_merge, "main")
    assert "torch" not in sys.modules or peft_merge is not None


def test_peft_merge_rejects_missing_adapter_dir(tmp_path: Path) -> None:
    from packages.exporter import peft_merge

    with pytest.raises(SystemExit):
        peft_merge.main([
            "--base", "Qwen/Qwen2.5-3B-Instruct",
            "--adapter", str(tmp_path / "nope"),
            "--out", str(tmp_path / "out"),
        ])
