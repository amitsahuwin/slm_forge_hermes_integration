"""Dataset discovery."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

DATA_ROOT = Path("/app/data/datasets")


class DatasetInfo(BaseModel):
    name: str
    train_count: int
    valid_count: int
    has_canary: bool
    description: str


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _read_description(readme: Path) -> str:
    if not readme.exists():
        return ""
    # First non-empty, non-heading line
    for line in readme.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


@router.get("", response_model=list[DatasetInfo])
def list_datasets() -> list[DatasetInfo]:
    if not DATA_ROOT.exists():
        return []
    out: list[DatasetInfo] = []
    for entry in sorted(DATA_ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        out.append(
            DatasetInfo(
                name=entry.name,
                train_count=_count_jsonl(entry / "train.jsonl"),
                valid_count=_count_jsonl(entry / "valid.jsonl"),
                has_canary=(entry / "canary.jsonl").exists(),
                description=_read_description(entry / "README.md"),
            )
        )
    return out
