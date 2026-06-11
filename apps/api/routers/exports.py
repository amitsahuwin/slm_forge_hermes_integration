"""Exports API — turn a completed run's adapter into iPhone-ready GGUF."""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, desc, select
from sse_starlette.sse import EventSourceResponse

from apps.api.middleware.auth import requires
from apps.api.models.export import Export, ExportStatus, QuantLevel
from apps.api.models.run import Run
from apps.api.services.db import get_session

router = APIRouter()


class ExportCreate(BaseModel):
    run_id: int
    # Default now includes Q5_K_M (good iPhone trade-off between Q4 quality
    # loss and Q8 size). Callers can still pass a custom list.
    quant_levels: list[QuantLevel] = [
        QuantLevel.Q4_K_M,
        QuantLevel.Q5_K_M,
        QuantLevel.Q8_0,
    ]


class ExportPatch(BaseModel):
    status: ExportStatus | None = None
    error_message: str | None = None
    progress_text: str | None = None
    fused_path: str | None = None
    gguf_f16_path: str | None = None
    gguf_q4_path: str | None = None
    gguf_q5_path: str | None = None
    gguf_q8_path: str | None = None
    gguf_f16_bytes: int | None = None
    gguf_q4_bytes: int | None = None
    gguf_q5_bytes: int | None = None
    gguf_q8_bytes: int | None = None


SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=Export)
def create_export(payload: ExportCreate, db: SessionDep) -> Export:
    run = db.get(Run, payload.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status.value != "completed":
        raise HTTPException(
            400,
            f"Run #{run.id} status is '{run.status.value}' — can only export completed runs",
        )
    if not run.adapter_path:
        raise HTTPException(400, f"Run #{run.id} has no adapter_path (nothing to fuse)")

    quants = ",".join(q.value for q in payload.quant_levels)
    export = Export(
        run_id=payload.run_id,
        base_model=run.base_model,
        method=run.method.value,
        quant_levels=quants,
    )
    db.add(export)
    db.commit()
    db.refresh(export)
    return export


@router.get("", response_model=list[Export])
def list_exports(
    db: SessionDep,
    status: ExportStatus | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[Export]:
    stmt = select(Export).order_by(desc(Export.created_at)).limit(limit)
    if status is not None:
        stmt = (
            select(Export).where(Export.status == status)
            .order_by(desc(Export.created_at)).limit(limit)
        )
    return list(db.exec(stmt).all())


@router.get("/{xid}", response_model=Export)
def get_export(xid: int, db: SessionDep) -> Export:
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")
    return e


@router.patch("/{xid}", response_model=Export)
def patch_export(xid: int, payload: ExportPatch, db: SessionDep) -> Export:
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(e, k, v)

    now = datetime.now(UTC)
    if payload.status == ExportStatus.FUSING and e.started_at is None:
        e.started_at = now
    if payload.status in {ExportStatus.COMPLETED, ExportStatus.FAILED, ExportStatus.CANCELLED}:
        e.completed_at = now

    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.get("/{xid}/stream")
async def stream_export(xid: int) -> EventSourceResponse:
    async def event_gen() -> AsyncGenerator[dict[str, str], None]:
        last_status: str | None = None
        last_progress: str | None = None
        terminal = {
            ExportStatus.COMPLETED.value, ExportStatus.FAILED.value, ExportStatus.CANCELLED.value,
        }
        from apps.api.services.db import engine
        from sqlmodel import Session as _Session

        while True:
            with _Session(engine) as s:
                e = s.get(Export, xid)
                if not e:
                    yield {"event": "error", "data": json.dumps({"message": "Export not found"})}
                    return

                if e.status.value != last_status or e.progress_text != last_progress:
                    last_status = e.status.value
                    last_progress = e.progress_text
                    yield {
                        "event": "update",
                        "data": json.dumps({
                            "status": e.status.value,
                            "progress_text": e.progress_text,
                            "gguf_q4_path": e.gguf_q4_path,
                            "gguf_q4_bytes": e.gguf_q4_bytes,
                        }),
                    }

                if e.status.value in terminal:
                    yield {"event": "done", "data": json.dumps({"status": e.status.value})}
                    return

            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())


def _to_container_path(host_path: str) -> str | None:
    """Translate a stored host path into the API container's view.

    The exporter (running on host) writes paths like
        /Users/<you>/.../<repo>/exports/<id>/gguf/model-Q4_K_M.gguf
    into the DB. Inside Docker, ``./exports`` is bind-mounted at
    ``/app/exports`` (see docker-compose.yml), so anything under the project's
    ``/exports/`` directory becomes ``/app/exports/<same suffix>``.

    Returns the translated path, or None if the host path doesn't contain an
    ``/exports/`` segment we can anchor on.
    """
    marker = "/exports/"
    idx = host_path.find(marker)
    if idx == -1:
        return None
    suffix = host_path[idx + len(marker) :]
    return f"/app/exports/{suffix}"


@router.get("/{xid}/download/{variant}")
def download_export(xid: int, variant: str, db: SessionDep) -> FileResponse:
    """Download a specific GGUF variant file. variant in {f16, q4, q5, q8}."""
    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")

    path_map = {
        "f16": e.gguf_f16_path,
        "q4": e.gguf_q4_path,
        "q5": e.gguf_q5_path,
        "q8": e.gguf_q8_path,
    }
    target = path_map.get(variant)
    if not target:
        raise HTTPException(404, f"Variant '{variant}' not available for this export")

    # Try the path as stored, then re-anchor via the project-relative ``/exports/``
    # mount point. This covers (a) the API running on host (path-as-stored works)
    # and (b) the API running in Docker (host path is meaningless, container path
    # via the bind mount works).
    candidates: list[str] = [target]
    container = _to_container_path(target)
    if container and container != target:
        candidates.append(container)
    # Also try the simple basename lookup under /app/exports/<export_id>/gguf/
    # as a last resort for legacy rows that stored a relative path.
    candidates.append(f"/app/exports/{xid}/gguf/{os.path.basename(target)}")

    for p in candidates:
        if p and os.path.exists(p):
            return FileResponse(
                p,
                filename=os.path.basename(p),
                media_type="application/octet-stream",
            )

    raise HTTPException(
        404,
        f"File not found on disk. Tried: {candidates}",
    )


@router.delete("/{xid}", status_code=204)
@requires("delete", "export")
def delete_export(xid: int, request: Request, db: SessionDep) -> None:
    """Delete an export and its on-disk artifacts."""
    import shutil
    from pathlib import Path

    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")

    db.delete(e)
    db.commit()

    export_dir = Path("/app/exports") / str(xid)
    if export_dir.exists():
        try:
            shutil.rmtree(export_dir)
        except OSError:
            pass
