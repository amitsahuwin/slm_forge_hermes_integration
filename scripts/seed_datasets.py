"""Copy bundled sample datasets into data/datasets/ in mlx_lm.lora's expected layout."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "data" / "datasets"  # already there in the patch

DATASETS = ["stock-analyst"]


def main() -> int:
    print(f"→ Seeding datasets under {SRC}")
    missing = []
    for d in DATASETS:
        td = SRC / d
        train = td / "train.jsonl"
        valid = td / "valid.jsonl"
        if not train.exists() or not valid.exists():
            missing.append(d)
            print(f"  ✗ {d}: missing train.jsonl or valid.jsonl")
        else:
            with train.open() as f:
                n_train = sum(1 for line in f if line.strip())
            with valid.open() as f:
                n_valid = sum(1 for line in f if line.strip())
            print(f"  ✓ {d}: {n_train} train / {n_valid} valid")
    if missing:
        print(f"\n✗ Missing datasets: {missing}", file=sys.stderr)
        return 1
    print("\n✓ All datasets ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
