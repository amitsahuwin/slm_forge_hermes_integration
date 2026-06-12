"""Phase P / A6 — pyproject trainer extras require Gemma-3/4-capable mlx-lm."""
from __future__ import annotations

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _trainer_extras() -> list[str]:
    text = PYPROJECT.read_text()
    block = re.search(r"trainer\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert block, "trainer optional-dependency group missing"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_mlx_floor_covers_gemma_support() -> None:
    deps = _trainer_extras()
    mlx = next(d for d in deps if re.match(r"mlx[>=]", d))
    mlx_lm = next(d for d in deps if d.startswith("mlx-lm"))
    # gemma3/gemma3n/gemma4/mistral3 model modules ship in these releases.
    assert mlx == "mlx>=0.31.2"
    assert mlx_lm == "mlx-lm>=0.31.3"
