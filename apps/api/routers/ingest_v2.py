"""Universal dataset ingestion v2.

Accepts any file (jsonl / json / csv / txt / md / unknown), detects the
format, and either parses it directly or falls back to Ollama-driven
conversion into chat-style records. Auto-splits into train/valid/canary.

Sibling to the existing `ingest` router (URL/scrape/s3 still live there).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

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
                warnings.append("Ollama returned nothing — keeping direct parse output.")
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
async def ingest_file(
    name: str = Form(...),
    file: UploadFile = File(...),
    description: str | None = Form(None),
    force_ollama: bool = Form(False),
) -> IngestFileResponse:
    """Upload any file, auto-convert, and write a new dataset to disk."""
    safe_name = _validate_name(name)
    dataset_dir = DATA_ROOT / safe_name
    if dataset_dir.exists():
        raise HTTPException(
            409,
            f"Dataset '{safe_name}' already exists. Pick a different name.",
        )

    content = await _read_capped(file)
    records, fmt, conversion, warnings = _convert(
        content, file.filename or "upload", force_ollama=force_ollama
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
        dataset_root=DATA_ROOT,
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


@router.post("/preview", response_model=IngestPreviewResponse)
async def preview_file(
    file: UploadFile = File(...),
    force_ollama: bool = Form(False),
) -> IngestPreviewResponse:
    """Detect the format and show what would be written, without writing."""
    content = await _read_capped(file)
    records, fmt, conversion, warnings = _convert(
        content, file.filename or "upload", force_ollama=force_ollama
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
