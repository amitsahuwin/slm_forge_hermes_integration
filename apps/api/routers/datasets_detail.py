"""Dataset detail + paginated rows.

This module is intended to be merged into ``apps/api/routers/datasets.py``
(see report). It defines new routes on its own ``APIRouter`` so the main
thread can either register it standalone at ``prefix="/api/v1/datasets"`` or
copy the route handlers into the existing router.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from apps.api.services.identity import current_identity
from apps.api.services.identity_paths import resolve_dataset

router = APIRouter()

# Preview row counts.
_TRAIN_PREVIEW = 5
_VALID_PREVIEW = 3
_CANARY_PREVIEW = 3


class DatasetDetail(BaseModel):
    name: str
    description: str
    readme_markdown: str
    train_count: int
    valid_count: int
    canary_count: int
    has_canary: bool
    # Char-based length stats over messages content across all splits.
    length_stats: dict
    train_preview: list[dict]
    valid_preview: list[dict]
    canary_preview: list[dict]


class RowsResponse(BaseModel):
    split: str
    offset: int
    limit: int
    total: int
    rows: list[dict]


# ─── helpers ──────────────────────────────────────────────────────────


def _dataset_dir(name: str, request: Request) -> Path:
    """Phase D.3 — resolve ``name`` against the caller's visible dirs
    (global samples + own + admin tenant-wide). 404 if not found / not
    allowed (opaque to avoid existence leaks)."""
    identity = current_identity(request)
    try:
        d = resolve_dataset(name, identity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if d is None or not d.is_dir():
        raise HTTPException(status_code=404, detail="Dataset not found")
    return d


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


def _read_readme(readme: Path) -> str:
    if not readme.exists():
        return ""
    try:
        return readme.read_text(encoding="utf-8")
    except OSError:
        return ""


def _iter_jsonl(path: Path):
    """Yield parsed rows from a .jsonl file; skip malformed lines."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except json.JSONDecodeError:
                continue


def _head_jsonl(path: Path, n: int) -> list[dict]:
    out: list[dict] = []
    for i, row in enumerate(_iter_jsonl(path)):
        if i >= n:
            break
        out.append(row)
    return out


def _slice_jsonl(path: Path, offset: int, limit: int) -> list[dict]:
    out: list[dict] = []
    end = offset + limit
    for i, row in enumerate(_iter_jsonl(path)):
        if i < offset:
            continue
        if i >= end:
            break
        out.append(row)
    return out


def _record_char_len(row: Any) -> int:
    """Char count for a chat-style record's content fields."""
    if not isinstance(row, dict):
        return 0
    msgs = row.get("messages")
    if isinstance(msgs, list):
        total = 0
        for m in msgs:
            if isinstance(m, dict):
                c = m.get("content")
                if isinstance(c, str):
                    total += len(c)
        return total
    # Fallback: stringify whatever value-shaped row this is.
    return len(json.dumps(row, ensure_ascii=False))


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    # Nearest-rank percentile.
    k = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def _length_stats(paths: list[Path]) -> dict:
    lens: list[int] = []
    for p in paths:
        for row in _iter_jsonl(p):
            lens.append(_record_char_len(row))
    if not lens:
        return {"min": 0, "p50": 0, "p90": 0, "max": 0, "mean": 0.0}
    lens.sort()
    return {
        "min": lens[0],
        "p50": _percentile(lens, 0.50),
        "p90": _percentile(lens, 0.90),
        "max": lens[-1],
        "mean": round(sum(lens) / len(lens), 2),
    }


# ─── routes ───────────────────────────────────────────────────────────


@router.get("/{name}", response_model=DatasetDetail)
def get_dataset_detail(name: str, request: Request) -> DatasetDetail:
    d = _dataset_dir(name, request)
    train_p = d / "train.jsonl"
    valid_p = d / "valid.jsonl"
    canary_p = d / "canary.jsonl"
    readme_p = d / "README.md"

    has_canary = canary_p.exists()
    return DatasetDetail(
        name=name,
        description=_read_description(readme_p),
        readme_markdown=_read_readme(readme_p),
        train_count=_count_jsonl(train_p),
        valid_count=_count_jsonl(valid_p),
        canary_count=_count_jsonl(canary_p) if has_canary else 0,
        has_canary=has_canary,
        length_stats=_length_stats([train_p, valid_p, canary_p]),
        train_preview=_head_jsonl(train_p, _TRAIN_PREVIEW),
        valid_preview=_head_jsonl(valid_p, _VALID_PREVIEW),
        canary_preview=_head_jsonl(canary_p, _CANARY_PREVIEW) if has_canary else [],
    )


@router.get("/{name}/rows", response_model=RowsResponse)
def get_dataset_rows(
    name: str,
    request: Request,
    split: str = Query("train", pattern="^(train|valid|canary)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> RowsResponse:
    d = _dataset_dir(name, request)
    path = d / f"{split}.jsonl"
    total = _count_jsonl(path)
    rows = _slice_jsonl(path, offset, limit) if total else []
    return RowsResponse(
        split=split,
        offset=offset,
        limit=limit,
        total=total,
        rows=rows,
    )
