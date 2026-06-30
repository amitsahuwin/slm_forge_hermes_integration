"""Data ingestion endpoints. Source-agnostic; produces mlx_lm.lora JSONL."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from apps.api.services import qa_store
from packages.ingest import local, s3, scrape, staging, url
from packages.ingest.formatter import ChatTemplate, format_row

log = logging.getLogger(__name__)

router = APIRouter()

DATA_ROOT = Path("/app/data/datasets")


class IngestPreview(BaseModel):
    """Returned to UI after parsing. The staging_id is required for finalize.

    PR-4: ``qa_id`` is the polling handle for the asynchronous quality scan
    (``GET /api/v1/ingest/qa/{qa_id}``). Optional — clients that don't care
    about quality warnings can ignore it.
    """
    staging_id: str
    source_type: Literal["upload", "url", "scrape", "s3"]
    format: str
    detected_fields: list[str]
    sample_rows: list[dict]
    total_rows: int
    qa_id: str | None = None


class QAWarningOut(BaseModel):
    severity: str
    category: str
    message: str
    affected_count: int = 0
    fix: str = ""


class QAStatusResponse(BaseModel):
    """PR-4 — polling payload for ``GET /api/v1/ingest/qa/{qa_id}``."""

    status: str  # "pending" | "ready" | "unavailable"
    overall_health: str | None = None
    summary: str | None = None
    warnings: list[QAWarningOut] = []
    ready_to_train: bool | None = None
    error: str | None = None


class FinalizeRequest(BaseModel):
    staging_id: str
    dataset_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-_]*$")
    prompt_field: str
    response_field: str
    template: ChatTemplate = "qwen"
    system_prompt: str | None = None
    valid_fraction: float = Field(default=0.15, ge=0.0, le=0.5)
    canary_fraction: float = Field(default=0.05, ge=0.0, le=0.3)
    overwrite: bool = False


class FinalizeResponse(BaseModel):
    dataset_name: str
    total_input_rows: int
    train_count: int
    valid_count: int
    canary_count: int
    skipped: int


def _build_preview(
    rows: list[dict],
    source_type: str,
    fmt: str,
    background: BackgroundTasks | None = None,
) -> IngestPreview:
    if not rows:
        raise HTTPException(400, "Source contained zero parseable rows")
    fields = sorted({k for r in rows[:50] for k in r.keys() if k})
    sid = staging.stash(rows)

    # PR-4 — enqueue the asynchronous quality scan if the feature is enabled
    # and the caller supplied a BackgroundTasks. Sample rows are capped to
    # the first 50 inside the scan itself.
    qa_id: str | None = None
    if background is not None and qa_store._enabled():
        qa_id = qa_store.new_id()
        qa_store.init_pending(qa_id)
        background.add_task(qa_store.run_qa, qa_id, rows[:50])

    return IngestPreview(
        staging_id=sid,
        source_type=source_type,  # type: ignore[arg-type]
        format=fmt,
        detected_fields=fields,
        sample_rows=rows[:5],
        total_rows=len(rows),
        qa_id=qa_id,
    )


# ─────────────────────────────────────────────────────────────
#   Source-specific PREVIEW endpoints
# ─────────────────────────────────────────────────────────────


@router.post("/upload/preview", response_model=IngestPreview)
async def preview_upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
) -> IngestPreview:
    content = await file.read()
    try:
        fmt, rows = local.parse_auto(file.filename or "upload", content)
    except Exception as e:
        raise HTTPException(400, f"Parse failed: {e}") from e
    return _build_preview(rows, "upload", fmt, background)


class UrlIn(BaseModel):
    url: str


@router.post("/url/preview", response_model=IngestPreview)
def preview_url(payload: UrlIn, background: BackgroundTasks) -> IngestPreview:
    try:
        fmt, rows = url.fetch_and_parse(payload.url)
    except Exception as e:
        raise HTTPException(400, f"URL fetch failed: {e}") from e
    return _build_preview(rows, "url", fmt, background)


@router.post("/scrape/preview", response_model=IngestPreview)
def preview_scrape(payload: UrlIn, background: BackgroundTasks) -> IngestPreview:
    try:
        row = scrape.scrape_url(payload.url)
    except Exception as e:
        raise HTTPException(400, f"Scrape failed: {e}") from e
    return _build_preview([row], "scrape", "html", background)


class S3In(BaseModel):
    s3_path: str
    access_key: str | None = None
    secret_key: str | None = None
    region: str | None = None


@router.post("/s3/preview", response_model=IngestPreview)
def preview_s3(payload: S3In, background: BackgroundTasks) -> IngestPreview:
    try:
        fmt, rows = s3.fetch_and_parse(
            payload.s3_path,
            access_key=payload.access_key,
            secret_key=payload.secret_key,
            region=payload.region,
        )
    except Exception as e:
        raise HTTPException(400, f"S3 fetch failed: {e}") from e
    return _build_preview(rows, "s3", fmt, background)


# ─────────────────────────────────────────────────────────────
#   PR-4 — QA polling endpoint
# ─────────────────────────────────────────────────────────────


@router.get("/qa/{qa_id}", response_model=QAStatusResponse)
def get_qa(qa_id: str) -> QAStatusResponse:
    """Polled by the UI every few seconds while ``status="pending"``.

    Returns 404 only when the qa_id was never registered (or expired out of
    the TTL). A still-running scan returns 200 with ``status="pending"``.
    """
    result = qa_store.get(qa_id)
    if result is None:
        raise HTTPException(404, f"qa_id {qa_id!r} not found or expired")
    return QAStatusResponse(
        status=result.status,
        overall_health=result.overall_health,
        summary=result.summary,
        warnings=[
            QAWarningOut(
                severity=w.severity,
                category=w.category,
                message=w.message,
                affected_count=w.affected_count,
                fix=w.fix,
            )
            for w in result.warnings
        ],
        ready_to_train=result.ready_to_train,
        error=result.error,
    )


# ─────────────────────────────────────────────────────────────
#   FINALIZE — read staged rows, format, split, write dataset
# ─────────────────────────────────────────────────────────────


@router.post("/finalize", response_model=FinalizeResponse)
def finalize(payload: FinalizeRequest, request: Request) -> FinalizeResponse:
    # Phase D.3 — writes go to the caller's per-user dataset dir.
    from apps.api.services.identity import current_identity
    from apps.api.services.identity_paths import safe_name, user_datasets_dir

    identity = current_identity(request)
    try:
        ds_name = safe_name(payload.dataset_name)
    except ValueError as e:
        raise HTTPException(400, f"Invalid dataset_name: {e}") from e
    dataset_dir = user_datasets_dir(identity) / ds_name
    if dataset_dir.exists() and not payload.overwrite:
        raise HTTPException(
            409, f"Dataset '{payload.dataset_name}' exists. Pass overwrite=true to replace."
        )

    try:
        rows = staging.read(payload.staging_id)
    except KeyError as e:
        raise HTTPException(
            404,
            f"{e}. Re-do the preview step — staging expires after 24 hours.",
        ) from e

    formatted: list[dict] = []
    skipped = 0
    for row in rows:
        out = format_row(
            row,
            prompt_field=payload.prompt_field,
            response_field=payload.response_field,
            template=payload.template,
            system_prompt=payload.system_prompt,
        )
        if out is None:
            skipped += 1
        else:
            formatted.append(out)

    if not formatted:
        raise HTTPException(
            400,
            f"No usable rows after formatting (all {skipped} skipped). "
            f"Check that prompt_field='{payload.prompt_field}' and "
            f"response_field='{payload.response_field}' exist and are non-empty in the source.",
        )

    # Deterministic shuffle by hashing the text content
    import hashlib

    def _key(s: str) -> int:
        return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)

    formatted.sort(key=lambda r: _key(r["text"]))

    n = len(formatted)
    n_canary = int(n * payload.canary_fraction) if payload.canary_fraction > 0 else 0
    n_valid = int(n * payload.valid_fraction) if payload.valid_fraction > 0 else 0
    n_train = max(0, n - n_canary - n_valid)

    # Guarantee at least 1 valid row if requested and the dataset is small
    if payload.valid_fraction > 0 and n_valid == 0 and n >= 2:
        n_valid = 1
        n_train = n - n_canary - n_valid

    train = formatted[:n_train]
    valid = formatted[n_train : n_train + n_valid]
    canary = formatted[n_train + n_valid :]

    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(dataset_dir / "train.jsonl", train)
    _write_jsonl(dataset_dir / "valid.jsonl", valid)
    if n_canary > 0:
        _write_jsonl(dataset_dir / "canary.jsonl", canary)

    readme = dataset_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {payload.dataset_name}\n\n"
            f"Ingested via SLM-Forge UI. "
            f"Template: {payload.template}. "
            f"Prompt field: `{payload.prompt_field}`, response field: `{payload.response_field}`.\n",
            encoding="utf-8",
        )

    # Clean up the staging file
    staging.discard(payload.staging_id)

    return FinalizeResponse(
        dataset_name=payload.dataset_name,
        total_input_rows=len(rows),
        train_count=len(train),
        valid_count=len(valid),
        canary_count=len(canary),
        skipped=skipped,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
