"""Universal dataset ingestion v2.

Accepts a dataset from any of four sources — file upload, URL, web scrape,
S3 — detects the format, parses it directly when recognized, or falls back
to Ollama-driven conversion. Auto-splits into train/valid/canary.

The four source endpoints all feed the same ``_convert`` pipeline so the
auto-convert + auto-split behavior is identical regardless of where bytes
came from.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from apps.api.middleware.auth import requires
from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.services import db
from apps.api.services.identity import current_identity
from apps.api.services.identity_paths import user_datasets_dir
from apps.api.services.ingest_settings import get_ingest_settings
from apps.api.services.storage.base import ObjectStore
from apps.api.services.storage.factory import get_object_store, tenant_key
from packages.dataset_ingest.converter import (
    auto_split,
    convert_via_ollama,
    detect_file_format,
    parse_known,
    write_dataset,
)
from packages.ratchet.hermes_bridge import HERMES_MODEL, OLLAMA_URL

log = logging.getLogger(__name__)

router = APIRouter()

# Chunk size for streaming a large upload to the object store. 1 MiB keeps
# per-request RSS flat regardless of file size while amortising syscalls.
_UPLOAD_CHUNK = 1024 * 1024

DATA_ROOT = Path("/app/data/datasets")
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

# When direct parsing yields fewer than this many records on a "soft" format
# (csv / json_array / markdown / plain_text), we fall back to Ollama.
_DIRECT_MIN_RECORDS = 8


# ─────────────────────────────────────────────────────────────
#   Response models
# ─────────────────────────────────────────────────────────────


class IngestFileResponse(BaseModel):
    name: str
    train: int
    valid: int
    canary: int
    format: str
    conversion: Literal["direct", "ollama"]


class LargeIngestResponse(BaseModel):
    job_id: str  # composite "ingest:<id>" surfaced in the Jobs tab
    name: str
    status: str
    detected_format: str
    raw_bytes: int


class IngestPreviewResponse(BaseModel):
    format: str
    conversion: Literal["direct", "ollama"]
    sample_records: list[dict]
    total_records: int
    predicted_train: int
    predicted_valid: int
    predicted_canary: int
    warnings: list[str]


# ─────────────────────────────────────────────────────────────
#   Helpers
# ─────────────────────────────────────────────────────────────


def _validate_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not name or not NAME_RE.match(name):
        raise HTTPException(
            400,
            "Invalid dataset name: must start with alphanumeric, and contain "
            "only lowercase letters, digits, hyphens, or underscores.",
        )
    if "/" in name or ".." in name:
        raise HTTPException(400, "Dataset name must not contain slashes.")
    return name


async def _read_capped(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(
            413,
            f"File too large ({len(content)} bytes). Limit is {MAX_BYTES} bytes (10 MB).",
        )
    return content


def _convert(
    content: bytes, filename: str, force_ollama: bool = False
) -> tuple[list[dict], str, Literal["direct", "ollama"], list[str]]:
    """Run the detect → parse → (maybe) Ollama pipeline.

    Returns (records, detected_format, conversion_path, warnings).
    """
    warnings: list[str] = []
    head = content[:65536]
    fmt = detect_file_format(filename, head)

    if force_ollama:
        text = content.decode("utf-8", errors="replace")
        records = convert_via_ollama(text, HERMES_MODEL, OLLAMA_URL)
        if not records:
            raise HTTPException(
                400,
                "Ollama returned zero parseable records. Try a different file "
                "or check that the Hermes model is pulled and running.",
            )
        return records, fmt, "ollama", warnings

    if fmt.startswith("jsonl_"):
        records = parse_known(fmt, content)
        if not records:
            raise HTTPException(400, "Parsed zero records from jsonl input.")
        return records, fmt, "direct", warnings

    if fmt in ("csv", "json_array", "markdown", "plain_text"):
        records: list[dict] = []
        try:
            records = parse_known(fmt, content)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Direct parse failed: {e}. Falling back to Ollama.")

        if len(records) < _DIRECT_MIN_RECORDS:
            warnings.append(
                f"Direct parse yielded {len(records)} records (< {_DIRECT_MIN_RECORDS}). "
                f"Falling back to Ollama conversion."
            )
            text = content.decode("utf-8", errors="replace")
            llm_records = convert_via_ollama(text, HERMES_MODEL, OLLAMA_URL)
            if llm_records:
                return llm_records, fmt, "ollama", warnings
            if records:
                warnings.append(
                    "Ollama returned nothing — keeping direct parse output."
                )
                return records, fmt, "direct", warnings
            raise HTTPException(
                400,
                f"Could not extract usable records from {filename}. "
                "Try a richer source file.",
            )
        return records, fmt, "direct", warnings

    # unknown → straight to Ollama
    text = content.decode("utf-8", errors="replace")
    records = convert_via_ollama(text, HERMES_MODEL, OLLAMA_URL)
    if not records:
        raise HTTPException(
            400,
            "Could not detect a known format and Ollama returned zero records.",
        )
    return records, fmt, "ollama", warnings


# ─────────────────────────────────────────────────────────────
#   Endpoints
# ─────────────────────────────────────────────────────────────


@router.post("/file", response_model=IngestFileResponse)
@requires("create", "dataset")
async def ingest_file(
    request: Request,
    name: str = Form(...),
    file: UploadFile = File(...),
    description: str | None = Form(None),
    force_ollama: bool = Form(False),
) -> IngestFileResponse:
    """Upload any file, auto-convert, and write a new dataset to disk."""
    safe_name = _validate_name(name)
    identity = current_identity(request)
    ds_root = user_datasets_dir(identity)
    dataset_dir = ds_root / safe_name
    if dataset_dir.exists():
        raise HTTPException(
            409,
            f"Dataset '{safe_name}' already exists. Pick a different name.",
        )

    content = await _read_capped(file)
    # _convert may run a blocking, multi-minute Ollama HTTP call; keep it off
    # the event loop so the API stays responsive (e.g. worker heartbeats).
    records, fmt, conversion, warnings = await asyncio.to_thread(
        _convert, content, file.filename or "upload", force_ollama
    )

    splits = auto_split(records)
    if not splits["valid"]:
        log.warning(
            "ingest_file: dataset %s has empty valid split (n=%d)",
            safe_name,
            len(records),
        )

    notes_lines = [
        f"Conversion path: {conversion}",
    ]
    if description:
        notes_lines.append(f"User description: {description}")
    if warnings:
        notes_lines.append("Warnings:")
        notes_lines.extend(f"- {w}" for w in warnings)

    write_dataset(
        name=safe_name,
        dataset_root=ds_root,
        splits=splits,
        source_format=fmt,
        source_filename=file.filename or "upload",
        conversion_notes="\n".join(notes_lines),
    )

    return IngestFileResponse(
        name=safe_name,
        train=len(splits["train"]),
        valid=len(splits["valid"]),
        canary=len(splits["canary"]),
        format=fmt,
        conversion=conversion,
    )


class _UploadTooLargeError(Exception):
    """Raised mid-stream when an upload breaches the configured byte cap."""


# Keep references to in-flight background tasks so the event loop doesn't
# garbage-collect them before ``_run_ingest_job`` finishes.
_INGEST_TASKS: set[asyncio.Task[None]] = set()


def _schedule_ingest(job_id: int) -> None:
    """Fire-and-forget the background runner for ``job_id``.

    Kept as a module-level indirection (not inlined) so tests can substitute
    a synchronous capture and drive the runner deterministically.
    """
    from apps.api.services.ingest_jobs import _run_ingest_job

    task = asyncio.create_task(_run_ingest_job(job_id))
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)


async def _stream_to_store(
    store: ObjectStore,
    raw_key: str,
    first: bytes,
    file: UploadFile,
    max_bytes: int,
) -> int:
    """Stream ``first`` + the rest of ``file`` to ``raw_key``, enforcing the
    byte cap mid-stream. Returns the total bytes written. On breach, deletes
    any partial object and raises :class:`_UploadTooLargeError`."""
    written = 0

    async def _body() -> AsyncIterator[bytes]:
        nonlocal written
        chunk = first
        while chunk:
            written += len(chunk)
            if written > max_bytes:
                raise _UploadTooLargeError
            yield chunk
            chunk = await file.read(_UPLOAD_CHUNK)

    try:
        await store.put(raw_key, _body(), content_type="application/octet-stream")
    except _UploadTooLargeError:
        await store.delete(raw_key)  # remove any partial (idempotent)
        raise
    return written


@router.post("/file/large", status_code=202, response_model=LargeIngestResponse)
@requires("create", "dataset")
async def ingest_file_large(
    request: Request,
    name: str = Form(...),
    file: UploadFile = File(...),
) -> LargeIngestResponse:
    """Stream a large pre-formatted JSONL/CSV upload to object storage and
    queue a background ingest job. Returns 202 with an ``ingest:<id>`` job id.

    Unlike ``/file``, this path never buffers the whole file in memory and is
    not gated by the 10 MB synchronous limit — only by
    ``SLM_FORGE_MAX_UPLOAD_BYTES``. It does not run the Ollama fallback; the
    input must already be a training-ready JSONL or CSV.
    """
    safe = _validate_name(name)
    identity = current_identity(request)
    settings = get_ingest_settings()

    if (user_datasets_dir(identity) / safe).exists():
        raise HTTPException(
            409, f"Dataset '{safe}' already exists. Pick a different name."
        )

    filename = file.filename or "upload"
    first = await file.read(65536)
    fmt = detect_file_format(filename, first)
    if not (fmt.startswith("jsonl_") or fmt == "csv"):
        raise HTTPException(
            422,
            "Large uploads must be pre-formatted JSONL (one object per line) "
            f"or CSV; detected '{fmt}'. Convert smaller files via /file.",
        )

    raw_name = "raw.csv" if fmt == "csv" else "raw.jsonl"
    raw_key = tenant_key(
        identity, kind="data", artifact_id=f"ingest-{uuid.uuid4().hex}", filename=raw_name
    )
    store = get_object_store(identity)

    try:
        total = await _stream_to_store(
            store, raw_key, first, file, settings.max_upload_bytes
        )
    except _UploadTooLargeError:
        raise HTTPException(
            413,
            f"File exceeds the {settings.max_upload_bytes}-byte upload limit.",
        ) from None

    with Session(db.engine) as session:
        job = IngestJob(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            dataset_name=safe,
            source_filename=filename,
            detected_format=fmt,
            raw_key=raw_key,
            raw_bytes=total,
            status=IngestStatus.QUEUED,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id
    assert job_id is not None

    _schedule_ingest(job_id)
    return LargeIngestResponse(
        job_id=f"ingest:{job_id}",
        name=safe,
        status=IngestStatus.QUEUED.value,
        detected_format=fmt,
        raw_bytes=total,
    )


@router.post("/preview", response_model=IngestPreviewResponse)
async def preview_file(
    file: UploadFile = File(...),
    force_ollama: bool = Form(False),
) -> IngestPreviewResponse:
    """Detect the format and show what would be written, without writing."""
    content = await _read_capped(file)
    records, fmt, conversion, warnings = await asyncio.to_thread(
        _convert, content, file.filename or "upload", force_ollama
    )
    splits = auto_split(records)

    return IngestPreviewResponse(
        format=fmt,
        conversion=conversion,
        sample_records=records[:5],
        total_records=len(records),
        predicted_train=len(splits["train"]),
        predicted_valid=len(splits["valid"]),
        predicted_canary=len(splits["canary"]),
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────
#   URL / Web scrape / S3 fetchers
# ─────────────────────────────────────────────────────────────


def _ensure_under_cap(content: bytes) -> bytes:
    if len(content) > MAX_BYTES:
        raise HTTPException(
            413,
            f"Source is too large ({len(content)} bytes). Limit is "
            f"{MAX_BYTES} bytes (10 MB).",
        )
    return content


def _fetch_url(url: str) -> tuple[bytes, str]:
    """Download bytes from a public HTTP(S) URL. Returns (content, filename)."""
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "SLM-Forge/0.1 (+local)"})
            r.raise_for_status()
            content = r.content
    except httpx.HTTPError as e:
        raise HTTPException(400, f"URL fetch failed: {e}") from e
    _ensure_under_cap(content)
    filename = url.rsplit("/", 1)[-1] or "download"
    return content, filename


def _fetch_scrape(url: str) -> tuple[bytes, str]:
    """Scrape main article text out of a URL via trafilatura.

    Returns the extracted text as UTF-8 bytes (treated as plain_text downstream).
    """
    from packages.ingest.scrape import scrape_url

    try:
        row = scrape_url(url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Scrape failed: {e}") from e
    title = row.get("title") or ""
    body = row.get("content") or ""
    if not body.strip():
        raise HTTPException(
            400,
            "Scrape returned no body content. The page may be JS-rendered "
            "(SPA) — try saving it locally and uploading the file instead.",
        )
    combined = f"# {title}\n\n{body}" if title else body
    content = combined.encode("utf-8")
    _ensure_under_cap(content)
    filename = (url.rsplit("/", 1)[-1] or "scraped") + ".txt"
    return content, filename


def _fetch_s3(
    s3_path: str,
    access_key: str | None,
    secret_key: str | None,
    region: str | None,
) -> tuple[bytes, str]:
    """Download bytes from an S3 object."""
    from packages.ingest import s3 as s3_mod

    if s3_mod.boto3 is None:
        raise HTTPException(
            500,
            "boto3 not installed in this image. Add the `ingest` extra "
            "(`uv sync --extra ingest`) and rebuild.",
        )
    try:
        bucket, key = s3_mod.parse_s3_path(s3_path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    session_kwargs: dict = {}
    if access_key and secret_key:
        session_kwargs["aws_access_key_id"] = access_key
        session_kwargs["aws_secret_access_key"] = secret_key
    if region:
        session_kwargs["region_name"] = region
    try:
        client = s3_mod.boto3.client("s3", **session_kwargs)
        obj = client.get_object(Bucket=bucket, Key=key)
        content = obj["Body"].read()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"S3 fetch failed: {e}") from e
    _ensure_under_cap(content)
    filename = key.rsplit("/", 1)[-1] or "object"
    return content, filename


# ─────────────────────────────────────────────────────────────
#   URL endpoints
# ─────────────────────────────────────────────────────────────


class UrlIngest(BaseModel):
    name: str
    url: str
    description: str | None = None
    force_ollama: bool = False


class UrlPreview(BaseModel):
    url: str
    force_ollama: bool = False


def _finalize_from_bytes(
    *,
    name: str,
    description: str | None,
    content: bytes,
    filename: str,
    force_ollama: bool,
    source_tag: str,
    dataset_root: Path,
) -> IngestFileResponse:
    safe_name = _validate_name(name)
    dataset_dir = dataset_root / safe_name
    if dataset_dir.exists():
        raise HTTPException(
            409, f"Dataset '{safe_name}' already exists. Pick a different name."
        )

    records, fmt, conversion, warnings = _convert(
        content, filename, force_ollama=force_ollama
    )
    splits = auto_split(records)
    if not splits["valid"]:
        log.warning(
            "ingest %s (%s): empty valid split (n=%d)",
            safe_name,
            source_tag,
            len(records),
        )

    notes_lines = [
        f"Source: {source_tag}",
        f"Conversion path: {conversion}",
    ]
    if description:
        notes_lines.append(f"User description: {description}")
    if warnings:
        notes_lines.append("Warnings:")
        notes_lines.extend(f"- {w}" for w in warnings)

    write_dataset(
        name=safe_name,
        dataset_root=dataset_root,
        splits=splits,
        source_format=fmt,
        source_filename=filename,
        conversion_notes="\n".join(notes_lines),
    )
    return IngestFileResponse(
        name=safe_name,
        train=len(splits["train"]),
        valid=len(splits["valid"]),
        canary=len(splits["canary"]),
        format=fmt,
        conversion=conversion,
    )


def _preview_from_bytes(
    *, content: bytes, filename: str, force_ollama: bool
) -> IngestPreviewResponse:
    records, fmt, conversion, warnings = _convert(
        content, filename, force_ollama=force_ollama
    )
    splits = auto_split(records)
    return IngestPreviewResponse(
        format=fmt,
        conversion=conversion,
        sample_records=records[:5],
        total_records=len(records),
        predicted_train=len(splits["train"]),
        predicted_valid=len(splits["valid"]),
        predicted_canary=len(splits["canary"]),
        warnings=warnings,
    )


@router.post("/from-url", response_model=IngestFileResponse)
def ingest_from_url(payload: UrlIngest, request: Request) -> IngestFileResponse:
    """Fetch a URL and write it as a new dataset."""
    identity = current_identity(request)
    ds_root = user_datasets_dir(identity)
    content, filename = _fetch_url(payload.url)
    return _finalize_from_bytes(
        name=payload.name,
        description=payload.description,
        content=content,
        filename=filename,
        force_ollama=payload.force_ollama,
        source_tag=f"url:{payload.url}",
        dataset_root=ds_root,
    )


@router.post("/from-url/preview", response_model=IngestPreviewResponse)
def preview_from_url(payload: UrlPreview) -> IngestPreviewResponse:
    """Preview what would be written from a URL — no disk side-effects."""
    content, filename = _fetch_url(payload.url)
    return _preview_from_bytes(
        content=content, filename=filename, force_ollama=payload.force_ollama
    )


# ─────────────────────────────────────────────────────────────
#   Scrape endpoints
# ─────────────────────────────────────────────────────────────


@router.post("/from-scrape", response_model=IngestFileResponse)
def ingest_from_scrape(payload: UrlIngest, request: Request) -> IngestFileResponse:
    """Scrape main text from a web page and write it as a new dataset."""
    identity = current_identity(request)
    ds_root = user_datasets_dir(identity)
    content, filename = _fetch_scrape(payload.url)
    return _finalize_from_bytes(
        name=payload.name,
        description=payload.description,
        content=content,
        filename=filename,
        force_ollama=payload.force_ollama,
        source_tag=f"scrape:{payload.url}",
        dataset_root=ds_root,
    )


@router.post("/from-scrape/preview", response_model=IngestPreviewResponse)
def preview_from_scrape(payload: UrlPreview) -> IngestPreviewResponse:
    """Preview what would be written from a scrape — no disk side-effects."""
    content, filename = _fetch_scrape(payload.url)
    return _preview_from_bytes(
        content=content, filename=filename, force_ollama=payload.force_ollama
    )


# ─────────────────────────────────────────────────────────────
#   S3 endpoints
# ─────────────────────────────────────────────────────────────


class S3Ingest(BaseModel):
    name: str
    s3_path: str  # s3://bucket/key OR https://...amazonaws.com URL
    access_key: str | None = None
    secret_key: str | None = None
    region: str | None = None
    description: str | None = None
    force_ollama: bool = False


class S3Preview(BaseModel):
    s3_path: str
    access_key: str | None = None
    secret_key: str | None = None
    region: str | None = None
    force_ollama: bool = False


@router.post("/from-s3", response_model=IngestFileResponse)
def ingest_from_s3(payload: S3Ingest, request: Request) -> IngestFileResponse:
    """Download an S3 object and write it as a new dataset."""
    identity = current_identity(request)
    ds_root = user_datasets_dir(identity)
    content, filename = _fetch_s3(
        payload.s3_path, payload.access_key, payload.secret_key, payload.region
    )
    return _finalize_from_bytes(
        name=payload.name,
        description=payload.description,
        content=content,
        filename=filename,
        force_ollama=payload.force_ollama,
        source_tag=f"s3:{payload.s3_path}",
        dataset_root=ds_root,
    )


@router.post("/from-s3/preview", response_model=IngestPreviewResponse)
def preview_from_s3(payload: S3Preview) -> IngestPreviewResponse:
    """Preview what would be written from S3 — no disk side-effects."""
    content, filename = _fetch_s3(
        payload.s3_path, payload.access_key, payload.secret_key, payload.region
    )
    return _preview_from_bytes(
        content=content, filename=filename, force_ollama=payload.force_ollama
    )
