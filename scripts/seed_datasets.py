"""Copy bundled sample datasets into data/datasets/global/ in mlx_lm.lora's expected layout.

Phase D.3 — bundled samples live under a ``global/`` subdir so they're
visible to every authenticated user, read-only. User-uploaded datasets
land under ``data/datasets/users/{tenant_id}/{user_id}/`` instead.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "data" / "datasets"
GLOBAL_ROOT = SRC_ROOT / "global"

DATASETS = ["stock-analyst"]


def main() -> int:
    GLOBAL_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"→ Seeding bundled datasets under {GLOBAL_ROOT}")
    missing = []
    for d in DATASETS:
        legacy = SRC_ROOT / d
        target = GLOBAL_ROOT / d

        # If the dataset is at the legacy flat path and not yet under
        # global/, move it. Idempotent — skip if already migrated.
        if legacy.is_dir() and not target.exists():
            shutil.move(str(legacy), str(target))
            print(f"  ↪ migrated {d} → global/{d}")
        elif legacy.is_dir() and target.exists() and legacy != target:
            # Both exist — prefer global/, remove legacy to avoid drift.
            shutil.rmtree(legacy)
            print(f"  ✓ {d} already under global/; cleaned legacy")

        train = target / "train.jsonl"
        valid = target / "valid.jsonl"
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
