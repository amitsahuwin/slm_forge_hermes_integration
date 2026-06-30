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

# Layout sentinels we MUST NOT treat as bundled samples — these are
# the Phase D.3 per-user / global containers themselves.
_RESERVED_TOP_LEVEL = {"global", "users"}


def _discover_legacy_datasets() -> list[str]:
    """Return every flat-layout dataset under ``SRC_ROOT`` that should
    be promoted to ``global/``.

    A "dataset" is a subdir of ``SRC_ROOT`` that:
      * is not one of the reserved layout dirs (``global``, ``users``),
      * is not a hidden dir,
      * contains a ``train.jsonl`` file (the minimum bundled-sample contract).
    """
    if not SRC_ROOT.is_dir():
        return []
    out: list[str] = []
    for child in sorted(SRC_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name in _RESERVED_TOP_LEVEL or child.name.startswith("."):
            continue
        if (child / "train.jsonl").exists():
            out.append(child.name)
    return out


def main() -> int:
    GLOBAL_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"→ Seeding bundled datasets under {GLOBAL_ROOT}")

    # Union of (1) datasets already living under global/ and (2) any
    # flat-layout dataset that should be promoted. This makes the script
    # idempotent AND auto-discovers new samples dropped at the flat path.
    already_global = sorted(
        d.name for d in GLOBAL_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")
    ) if GLOBAL_ROOT.exists() else []
    legacy = _discover_legacy_datasets()
    datasets = sorted(set(already_global) | set(legacy))

    if not datasets:
        print("  (no datasets to seed)")
        return 0

    missing = []
    for d in datasets:
        legacy_path = SRC_ROOT / d
        target = GLOBAL_ROOT / d

        # If the dataset is at the flat path and not yet under global/,
        # move it. Idempotent — skip if already migrated.
        if legacy_path.is_dir() and not target.exists():
            shutil.move(str(legacy_path), str(target))
            print(f"  ↪ migrated {d} → global/{d}")
        elif legacy_path.is_dir() and target.exists() and legacy_path != target:
            # Both exist — prefer global/, remove legacy to avoid drift.
            shutil.rmtree(legacy_path)
            print(f"  ✓ {d} already under global/; cleaned legacy duplicate")

        train = target / "train.jsonl"
        valid = target / "valid.jsonl"
        if not train.exists():
            missing.append(d)
            print(f"  ✗ {d}: missing train.jsonl")
            continue
        with train.open() as f:
            n_train = sum(1 for line in f if line.strip())
        if valid.exists():
            with valid.open() as f:
                n_valid = sum(1 for line in f if line.strip())
            print(f"  ✓ {d}: {n_train} train / {n_valid} valid")
        else:
            # Some bundled samples are train-only; that's fine — UI will
            # render train_count and leave valid_count at 0.
            print(f"  ✓ {d}: {n_train} train (no valid.jsonl)")
    if missing:
        print(f"\n✗ Missing required files: {missing}", file=sys.stderr)
        return 1
    print(f"\n✓ All {len(datasets)} datasets ready under global/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
