"""Dataset discovery + archive download (Phase R + D.3)."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from apps.api.services.identity import current_identity
from apps.api.services.identity_paths import (
    resolve_dataset,
    visible_dataset_dirs,
)

router = APIRouter()


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
    for line in readme.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def _scan_dir(parent: Path, depth: int = 1) -> list[Path]:
    """Yield candidate dataset dirs under ``parent``.

    ``depth=1`` for ``global/<name>/`` and ``users/{tenant}/{user}/<name>/``
    when the parent IS the user dir. For tenant-admin, the parent is
    ``users/{tenant}/`` which has structure ``users/{tenant}/{user}/<name>/``
    — depth=2.
    """
    if not parent.exists():
        return []
    if depth == 1:
        return [
            p for p in parent.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ]
    out: list[Path] = []
    for inner in parent.iterdir():
        if not inner.is_dir() or inner.name.startswith("."):
            continue
        out.extend(_scan_dir(inner, depth=depth - 1))
    return out


@router.get("", response_model=list[DatasetInfo])
def list_datasets(request: Request) -> list[DatasetInfo]:
    """Phase D.3 — global samples ∪ caller's own datasets (admin sees
    every user in their tenant). Datasets with duplicate names across
    layers are deduped, keeping the first (global wins)."""
    identity = current_identity(request)
    dirs = visible_dataset_dirs(identity)
    # global_datasets_dir is depth=1; users/{tenant} is depth=2;
    # users/{tenant}/{user} is depth=1.
    seen: set[str] = set()
    out: list[DatasetInfo] = []
    for base in dirs:
        # Figure depth from the base path: ends with ``global`` or
        # a user dir → depth 1; ends with a tenant dir → depth 2.
        depth = 2 if base.name == identity.tenant_id else 1
        for entry in sorted(_scan_dir(base, depth=depth)):
            if entry.name in seen:
                continue
            seen.add(entry.name)
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
def download_dataset_archive(name: str, request: Request) -> Response:
    """Phase R + D.3 — stream a dataset dir as tar.gz for remote workers.
    Caller must own the dataset (or be tenant-admin); 404 otherwise.
    """
    identity = current_identity(request)
    try:
        ds_dir = resolve_dataset(name, identity)
    except ValueError as e:
        raise HTTPException(422, f"Invalid dataset name: {e}") from e
    if ds_dir is None or not ds_dir.is_dir():
        raise HTTPException(404, f"Dataset '{name}' not found")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(ds_dir, arcname=name)
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}.tar.gz"'},
    )
