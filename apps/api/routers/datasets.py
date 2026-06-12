"""Dataset discovery + archive download (Phase R)."""
from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

router = APIRouter()

DATA_ROOT = Path("/app/data/datasets")

# Dataset names are single path segments: alnum start, then alnum/._- only.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


@router.get("/{name}/archive")
def download_dataset_archive(name: str) -> Response:
    """Phase R — stream a dataset dir as tar.gz for remote workers.

    Members are rooted at ``<name>/`` so extraction recreates the standard
    ``data/datasets/<name>/`` layout on the worker.
    """
    if not _NAME_RE.match(name) or ".." in name:
        raise HTTPException(422, f"Invalid dataset name: {name!r}")
    ds_dir = DATA_ROOT / name
    if not ds_dir.is_dir():
        raise HTTPException(404, f"Dataset '{name}' not found")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(ds_dir, arcname=name)
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}.tar.gz"'},
    )
