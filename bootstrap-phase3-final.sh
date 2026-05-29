#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 3 patch (data ingestion)                          ║
# ║                                                                      ║
# ║  Adds:                                                               ║
# ║    • Local file upload (JSONL / CSV)                                 ║
# ║    • URL fetch (JSONL / CSV)                                         ║
# ║    • Web scrape (trafilatura — static HTML only)                     ║
# ║    • S3 fetch (boto3, creds in form, never stored)                   ║
# ║    • Schema mapping wizard (preview → map → finalize)                ║
# ║    • Chat-template formatter (gemma / llama3 / qwen / raw)           ║
# ║    • New Dataset wizard UI (3 steps)                                 ║
# ║    • ingest_dataset Hermes skill                                     ║
# ║    • Persistent ingest staging — survives API restarts               ║
# ║                                                                      ║
# ║  Apply AFTER Phase 2 is verified working:                            ║
# ║    chmod +x bootstrap_phase3.sh                                      ║
# ║    ./bootstrap_phase3.sh                                             ║
# ║    make rebuild                                                      ║
# ║    make dev                                                          ║
# ║                                                                      ║
# ║  Then http://localhost:5173/datasets/new                             ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Applying Phase 3 patch..."

# Sanity check: Phase 2 should be present
if [ ! -f "apps/api/routers/sessions.py" ]; then
    echo ""
    echo "⚠  apps/api/routers/sessions.py not found."
    echo "   Did you apply Phase 2 yet? Phase 3 builds on Phase 2's API structure."
    echo "   Apply Phase 2 first, verify it works, then re-run this script."
    echo ""
    read -p "Continue anyway? (y/N) " yn
    case "$yn" in
        [Yy]*) ;;
        *) echo "Aborted."; exit 1;;
    esac
fi

mkdir -p packages/ingest
mkdir -p apps/web/src/pages
mkdir -p data/.ingest_staging

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  1. packages/ingest/formatter.py                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/formatter.py <<'EOF'
"""Convert raw row dicts into mlx_lm.lora's chat-templated JSONL format.

Output schema (what mlx_lm.lora expects): each line is {"text": "..."}
containing the fully-templated prompt + response.
"""
from __future__ import annotations

from typing import Literal

ChatTemplate = Literal["gemma", "llama3", "qwen", "raw"]


def _gemma_template(user: str, model: str) -> str:
    return (
        f"<start_of_turn>user\n{user}<end_of_turn>\n"
        f"<start_of_turn>model\n{model}<end_of_turn>"
    )


def _llama3_template(user: str, model: str) -> str:
    return (
        f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n"
        f"{user}<|eot_id|>"
        f"<|start_header_id|>assistant<|end_header_id|>\n\n"
        f"{model}<|eot_id|>"
    )


def _qwen_template(user: str, model: str) -> str:
    return (
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{model}<|im_end|>"
    )


def format_row(
    row: dict,
    *,
    prompt_field: str,
    response_field: str,
    template: ChatTemplate = "qwen",
    system_prompt: str | None = None,
) -> dict[str, str] | None:
    """Convert one source row into mlx_lm.lora's text format.

    Returns None if required fields are missing or empty.
    """
    if prompt_field not in row or response_field not in row:
        return None
    user = str(row[prompt_field]).strip()
    model = str(row[response_field]).strip()
    if not user or not model:
        return None

    if system_prompt:
        user = f"{system_prompt}\n\n{user}"

    if template == "gemma":
        text = _gemma_template(user, model)
    elif template == "llama3":
        text = _llama3_template(user, model)
    elif template == "qwen":
        text = _qwen_template(user, model)
    else:  # raw
        text = f"{user}\n\n{model}"

    return {"text": text}


def auto_detect_template(base_model: str) -> ChatTemplate:
    """Guess the right chat template from the base model HF id."""
    m = base_model.lower()
    if "qwen" in m:
        return "qwen"
    if "llama" in m:
        return "llama3"
    if "gemma" in m:
        return "gemma"
    return "raw"
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  2. packages/ingest/local.py — parse uploaded files                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/local.py <<'EOF'
"""Parse uploaded files into row dicts. Supports JSONL and CSV."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator


def parse_jsonl(content: bytes) -> Iterator[dict]:
    """Yield one dict per non-empty line."""
    text = content.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {lineno}: invalid JSON ({e})") from e


def parse_csv(content: bytes) -> Iterator[dict]:
    """Yield one dict per CSV row (first row used as headers)."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    for row in reader:
        yield {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }


def parse_json_array(content: bytes) -> Iterator[dict]:
    """For files that are a JSON array of objects, not JSONL."""
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("JSON file is not a top-level array")
    for item in data:
        if isinstance(item, dict):
            yield item


def detect_format(filename: str, content: bytes) -> str:
    """Return 'jsonl' | 'csv' | 'json' | 'unknown'."""
    name = filename.lower()
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".json"):
        # could be JSONL with .json extension OR JSON array — sniff
        head = content[:200].lstrip()
        if head.startswith(b"["):
            return "json"
        return "jsonl"
    # No extension — sniff
    head = content[:1024].decode("utf-8", errors="replace").strip()
    if head.startswith("[") and head.endswith("]"):
        return "json"
    if head.startswith("{"):
        return "jsonl"
    first_line = head.split("\n", 1)[0]
    if "," in first_line and "{" not in first_line:
        return "csv"
    return "unknown"


def parse_auto(filename: str, content: bytes) -> tuple[str, list[dict]]:
    """Parse a file and return (format, rows). Raises ValueError on unknown."""
    fmt = detect_format(filename, content)
    if fmt == "jsonl":
        return fmt, list(parse_jsonl(content))
    if fmt == "csv":
        return fmt, list(parse_csv(content))
    if fmt == "json":
        return fmt, list(parse_json_array(content))
    raise ValueError(
        f"Could not detect format of {filename!r}. "
        "Expected .jsonl, .csv, or .json (array of objects)."
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  3. packages/ingest/url.py — fetch remote files                      ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/url.py <<'EOF'
"""Fetch a remote URL and parse as JSONL/CSV/JSON."""
from __future__ import annotations

import httpx

from packages.ingest.local import parse_auto

MAX_BYTES = 100 * 1024 * 1024  # 100 MB hard cap


def fetch_and_parse(url: str) -> tuple[str, list[dict]]:
    """Download the URL and parse it. Returns (format, rows)."""
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        try:
            head = c.head(url)
            cl = head.headers.get("content-length")
            if cl and int(cl) > MAX_BYTES:
                raise ValueError(
                    f"File too large: {int(cl) / 1e6:.1f} MB (max {MAX_BYTES / 1e6:.0f} MB)"
                )
        except httpx.HTTPError:
            pass  # some servers don't support HEAD

        r = c.get(url)
        r.raise_for_status()
        content = r.content
        if len(content) > MAX_BYTES:
            raise ValueError(f"File too large: {len(content) / 1e6:.1f} MB")

    name = url.rsplit("/", 1)[-1] or "download"
    return parse_auto(name, content)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  4. packages/ingest/scrape.py — single-URL static-HTML scrape        ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/scrape.py <<'EOF'
"""Scrape one URL → main content text via trafilatura.

Static HTML only. JS-heavy SPAs won't work (trafilatura sees no rendered text).
For those, the user should save the rendered page and upload it as a file.
"""
from __future__ import annotations

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None  # type: ignore[assignment]


def scrape_url(url: str) -> dict:
    """Fetch one URL, extract main content, return a single row dict.

    Returns: {"url": ..., "title": ..., "content": ...}
    """
    if trafilatura is None:
        raise RuntimeError("trafilatura not installed. Run: uv sync --extra ingest")

    with httpx.Client(timeout=30, follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": "SLM-Forge/0.1 (+local)"})
        r.raise_for_status()
        html = r.text

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if not extracted:
        raise ValueError(
            f"trafilatura found no main content at {url}. "
            "This is usually a JS-heavy SPA — try saving the rendered page and uploading it as a file."
        )

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title
    except Exception:  # noqa: BLE001
        pass

    return {"url": url, "title": title, "content": extracted}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  5. packages/ingest/s3.py — S3 fetch                                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/s3.py <<'EOF'
"""Fetch an S3 object and parse it. Credentials come from the request payload."""
from __future__ import annotations

import re
from urllib.parse import urlparse

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

from packages.ingest.local import parse_auto

_S3_PATH_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def parse_s3_path(s3_path: str) -> tuple[str, str]:
    """Parse 's3://bucket/key' → (bucket, key). Also accepts https://...amazonaws.com URLs."""
    m = _S3_PATH_RE.match(s3_path)
    if m:
        return m.group(1), m.group(2)
    p = urlparse(s3_path)
    if p.hostname and p.hostname.endswith("amazonaws.com"):
        bucket = p.hostname.split(".")[0]
        key = p.path.lstrip("/")
        return bucket, key
    raise ValueError(f"Could not parse S3 path: {s3_path}")


def fetch_and_parse(
    s3_path: str,
    *,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
) -> tuple[str, list[dict]]:
    """Download s3://bucket/key, parse, return (format, rows)."""
    if boto3 is None:
        raise RuntimeError("boto3 not installed. Run: uv sync --extra ingest")

    bucket, key = parse_s3_path(s3_path)

    session_kwargs: dict = {}
    if access_key and secret_key:
        session_kwargs["aws_access_key_id"] = access_key
        session_kwargs["aws_secret_access_key"] = secret_key
    if region:
        session_kwargs["region_name"] = region

    session = boto3.Session(**session_kwargs)
    s3 = session.client("s3")

    resp = s3.get_object(Bucket=bucket, Key=key)
    content = resp["Body"].read()

    return parse_auto(key, content)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  6. packages/ingest/staging.py — persistent ingest staging          ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > packages/ingest/staging.py <<'EOF'
"""Persistent staging for ingest previews.

Flow:
  1. Source endpoint parses raw rows → writes them to data/.ingest_staging/<id>.jsonl
  2. Preview response includes the staging_id
  3. Finalize endpoint reads the staging file by id and writes the final dataset
  4. Stale stages older than STAGE_TTL_HOURS are auto-cleaned

This survives API restarts (unlike an in-memory cache) and doesn't lose rows
between preview and finalize (which a naive cache approach would).
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

STAGE_DIR = Path("/app/data/.ingest_staging")
STAGE_TTL_HOURS = 24


def _ensure_dir() -> Path:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STAGE_DIR


def _cleanup_stale() -> None:
    """Remove staging files older than STAGE_TTL_HOURS."""
    _ensure_dir()
    cutoff = time.time() - STAGE_TTL_HOURS * 3600
    for f in STAGE_DIR.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def stash(rows: list[dict]) -> str:
    """Write rows to a staging file, return an opaque id."""
    _cleanup_stale()
    sid = secrets.token_urlsafe(12)
    path = STAGE_DIR / f"{sid}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return sid


def read(sid: str) -> list[dict]:
    """Read rows from a staging file by id. Raises KeyError if not found."""
    if not sid or "/" in sid or ".." in sid:
        raise KeyError("invalid staging id")
    path = STAGE_DIR / f"{sid}.jsonl"
    if not path.exists():
        raise KeyError(f"staging {sid} not found (may have expired or been finalized)")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def discard(sid: str) -> None:
    """Remove a staging file (called after successful finalize)."""
    if not sid or "/" in sid or ".." in sid:
        return
    (STAGE_DIR / f"{sid}.jsonl").unlink(missing_ok=True)
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  7. apps/api/routers/ingest.py — the API surface                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/routers/ingest.py <<'EOF'
"""Data ingestion endpoints. Source-agnostic; produces mlx_lm.lora JSONL."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from packages.ingest import local, s3, scrape, staging, url
from packages.ingest.formatter import ChatTemplate, format_row

log = logging.getLogger(__name__)

router = APIRouter()

DATA_ROOT = Path("/app/data/datasets")


class IngestPreview(BaseModel):
    """Returned to UI after parsing. The staging_id is required for finalize."""
    staging_id: str
    source_type: Literal["upload", "url", "scrape", "s3"]
    format: str
    detected_fields: list[str]
    sample_rows: list[dict]
    total_rows: int


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


def _build_preview(rows: list[dict], source_type: str, fmt: str) -> IngestPreview:
    if not rows:
        raise HTTPException(400, "Source contained zero parseable rows")
    fields = sorted({k for r in rows[:50] for k in r.keys() if k})
    sid = staging.stash(rows)
    return IngestPreview(
        staging_id=sid,
        source_type=source_type,  # type: ignore[arg-type]
        format=fmt,
        detected_fields=fields,
        sample_rows=rows[:5],
        total_rows=len(rows),
    )


# ─────────────────────────────────────────────────────────────
#   Source-specific PREVIEW endpoints
# ─────────────────────────────────────────────────────────────


@router.post("/upload/preview", response_model=IngestPreview)
async def preview_upload(file: UploadFile = File(...)) -> IngestPreview:
    content = await file.read()
    try:
        fmt, rows = local.parse_auto(file.filename or "upload", content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Parse failed: {e}") from e
    return _build_preview(rows, "upload", fmt)


class UrlIn(BaseModel):
    url: str


@router.post("/url/preview", response_model=IngestPreview)
def preview_url(payload: UrlIn) -> IngestPreview:
    try:
        fmt, rows = url.fetch_and_parse(payload.url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"URL fetch failed: {e}") from e
    return _build_preview(rows, "url", fmt)


@router.post("/scrape/preview", response_model=IngestPreview)
def preview_scrape(payload: UrlIn) -> IngestPreview:
    try:
        row = scrape.scrape_url(payload.url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Scrape failed: {e}") from e
    return _build_preview([row], "scrape", "html")


class S3In(BaseModel):
    s3_path: str
    access_key: str | None = None
    secret_key: str | None = None
    region: str | None = None


@router.post("/s3/preview", response_model=IngestPreview)
def preview_s3(payload: S3In) -> IngestPreview:
    try:
        fmt, rows = s3.fetch_and_parse(
            payload.s3_path,
            access_key=payload.access_key,
            secret_key=payload.secret_key,
            region=payload.region,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"S3 fetch failed: {e}") from e
    return _build_preview(rows, "s3", fmt)


# ─────────────────────────────────────────────────────────────
#   FINALIZE — read staged rows, format, split, write dataset
# ─────────────────────────────────────────────────────────────


@router.post("/finalize", response_model=FinalizeResponse)
def finalize(payload: FinalizeRequest) -> FinalizeResponse:
    dataset_dir = DATA_ROOT / payload.dataset_name
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
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  8. UPDATE apps/api/main.py — mount ingest router                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/api/main.py <<'EOF'
"""SLM-Forge API — Phase 3."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from apps.api.routers import datasets, ingest, models, runs, sessions
from apps.api.services.db import init_db


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    python: str
    capabilities: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    init_db()
    yield


app = FastAPI(title="SLM-Forge API", version="0.4.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router, prefix="/api/v1/runs", tags=["runs"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(datasets.router, prefix="/api/v1/datasets", tags=["datasets"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(ingest.router, prefix="/api/v1/ingest", tags=["ingest"])


@app.get("/")
async def root() -> dict[str, Any]:
    return {"name": "SLM-Forge API", "version": "0.4.0", "docs": "/docs"}


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import sys
    return HealthResponse(
        status="ok",
        version="0.4.0",
        phase="Phase 3 — data ingestion",
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        capabilities={
            "trainer": True,
            "autoresearch": True,
            "ingestion": True,
            "export_gguf": False,
            "hermes_bridge": True,
        },
    )
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  9. Hermes skill for dataset ingestion                               ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > .hermes-skills/ingest_dataset.md <<'EOF'
# Skill: Ingest Dataset

Given a source description (URL / file extension / S3 path / arbitrary text),
recommend the right ingestion endpoint and suggest schema mapping.

## Decision table

| Source signal | Endpoint | Notes |
|---|---|---|
| `s3://...` or `*.amazonaws.com/...` | `POST /api/v1/ingest/s3/preview` | needs creds |
| URL ending in `.jsonl`, `.ndjson`, `.csv`, `.json` | `POST /api/v1/ingest/url/preview` | direct download |
| User uploaded a file | `POST /api/v1/ingest/upload/preview` | multipart/form-data |
| Generic web page URL | `POST /api/v1/ingest/scrape/preview` | trafilatura main-content extraction (static HTML only) |

## Schema mapping heuristics

After preview, the API returns `detected_fields`. Common mappings:
- `prompt_field` ← typically: `question`, `prompt`, `instruction`, `input`, `user`, `query`
- `response_field` ← typically: `answer`, `response`, `output`, `completion`, `assistant`, `content`

For HuggingFace `alpaca`-style datasets: `instruction` + `output`.
For `oasst`/`sharegpt`-style: requires per-turn flattening (not supported by this skill — tell user to preprocess).

## Output (JSON)

```json
{
  "endpoint": "/api/v1/ingest/url/preview",
  "prompt_field_guess": "question",
  "response_field_guess": "answer",
  "template_guess": "qwen",
  "notes": "1-sentence explanation"
}
```
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  10. UPDATE apps/web/src/lib/api.ts — add ingest API                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
# We append to the existing api.ts (Phase 2 left it intact)
cat >> apps/web/src/lib/api.ts <<'EOF'

// ─── Phase 3 ingestion ────────────────────────────────────────

export type IngestPreview = {
  staging_id: string;
  source_type: 'upload' | 'url' | 'scrape' | 's3';
  format: string;
  detected_fields: string[];
  sample_rows: Record<string, unknown>[];
  total_rows: number;
};

export type FinalizeResponse = {
  dataset_name: string;
  total_input_rows: number;
  train_count: number;
  valid_count: number;
  canary_count: number;
  skipped: number;
};

export const ingest = {
  async previewUpload(file: File): Promise<IngestPreview> {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${API_URL}/api/v1/ingest/upload/preview`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`Upload failed: HTTP ${r.status} — ${await r.text()}`);
    return r.json();
  },
  previewUrl: (u: string) =>
    fetch(`${API_URL}/api/v1/ingest/url/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: u }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`URL fetch failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  previewScrape: (u: string) =>
    fetch(`${API_URL}/api/v1/ingest/scrape/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: u }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Scrape failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  previewS3: (args: { s3_path: string; access_key?: string; secret_key?: string; region?: string }) =>
    fetch(`${API_URL}/api/v1/ingest/s3/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`S3 fetch failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  finalize: (args: {
    staging_id: string;
    dataset_name: string;
    prompt_field: string;
    response_field: string;
    template: 'gemma' | 'llama3' | 'qwen' | 'raw';
    system_prompt?: string;
    valid_fraction?: number;
    canary_fraction?: number;
    overwrite?: boolean;
  }) =>
    fetch(`${API_URL}/api/v1/ingest/finalize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Finalize failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<FinalizeResponse>;
    }),
};
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  11. UPDATE apps/web/src/App.tsx — add /datasets/new route           ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/App.tsx <<'EOF'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import NewDataset from './pages/NewDataset';
import NewRun from './pages/NewRun';
import NewSession from './pages/NewSession';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';
import SessionDetail from './pages/SessionDetail';
import Sessions from './pages/Sessions';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-7xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/new" element={<NewSession />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/new" element={<NewRun />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/new" element={<NewDataset />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  12. UPDATE apps/web/src/pages/Datasets.tsx — add "New" button       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/Datasets.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type DatasetInfo, api } from '../lib/api';

export default function Datasets() {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .listDatasets()
        .then((d) => alive && setDatasets(d))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Available training datasets under <code className="text-zinc-400">data/datasets/</code>.
          </p>
        </div>
        <Link
          to="/datasets/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Dataset
        </Link>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {datasets === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : datasets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No datasets yet.{' '}
          <Link to="/datasets/new" className="text-emerald-400 hover:underline">
            Ingest your first dataset →
          </Link>
        </div>
      ) : (
        <ul className="space-y-3">
          {datasets.map((d) => (
            <li key={d.name} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
              <div className="flex items-baseline justify-between gap-4">
                <h3 className="font-mono text-sm font-semibold text-zinc-100">{d.name}</h3>
                <div className="font-mono text-xs text-zinc-500">
                  {d.train_count} train · {d.valid_count} valid
                  {d.has_canary && ' · canary ✓'}
                </div>
              </div>
              {d.description && <p className="mt-1.5 text-sm text-zinc-400">{d.description}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  13. ADD apps/web/src/pages/NewDataset.tsx — the wizard              ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/pages/NewDataset.tsx <<'EOF'
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type IngestPreview, ingest } from '../lib/api';

type SourceType = 'upload' | 'url' | 'scrape' | 's3';
type Template = 'gemma' | 'llama3' | 'qwen' | 'raw';

export default function NewDataset() {
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2>(1);
  const [source, setSource] = useState<SourceType>('upload');
  const [preview, setPreview] = useState<IngestPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 inputs
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [s3Path, setS3Path] = useState('');
  const [s3Key, setS3Key] = useState('');
  const [s3Secret, setS3Secret] = useState('');
  const [s3Region, setS3Region] = useState('us-east-1');

  // Step 2 inputs
  const [datasetName, setDatasetName] = useState('');
  const [promptField, setPromptField] = useState('');
  const [responseField, setResponseField] = useState('');
  const [template, setTemplate] = useState<Template>('qwen');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [validFraction, setValidFraction] = useState(0.15);
  const [canaryFraction, setCanaryFraction] = useState(0.05);

  async function doPreview() {
    setError(null);
    setBusy(true);
    try {
      let p: IngestPreview;
      if (source === 'upload') {
        if (!file) throw new Error('Select a file');
        p = await ingest.previewUpload(file);
      } else if (source === 'url') {
        if (!url) throw new Error('URL required');
        p = await ingest.previewUrl(url);
      } else if (source === 'scrape') {
        if (!url) throw new Error('URL required');
        p = await ingest.previewScrape(url);
      } else {
        if (!s3Path) throw new Error('S3 path required');
        p = await ingest.previewS3({
          s3_path: s3Path,
          access_key: s3Key || undefined,
          secret_key: s3Secret || undefined,
          region: s3Region || undefined,
        });
      }
      setPreview(p);
      // best-effort field guesses
      const fields = p.detected_fields;
      const promptGuess = fields.find((f) =>
        /^(question|prompt|instruction|input|user|query|content)$/i.test(f),
      );
      const respGuess = fields.find((f) =>
        /^(answer|response|output|completion|assistant)$/i.test(f),
      );
      setPromptField(promptGuess ?? fields[0] ?? '');
      setResponseField(respGuess ?? fields[1] ?? fields[0] ?? '');
      setStep(2);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doFinalize() {
    if (!preview) return;
    setError(null);
    setBusy(true);
    try {
      await ingest.finalize({
        staging_id: preview.staging_id,
        dataset_name: datasetName,
        prompt_field: promptField,
        response_field: responseField,
        template,
        system_prompt: systemPrompt || undefined,
        valid_fraction: validFraction,
        canary_fraction: canaryFraction,
        overwrite: true,
      });
      navigate('/datasets');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Dataset</h1>
        <p className="mt-1 text-sm text-zinc-500">
          {step === 1
            ? 'Step 1 of 2 — pick a source and preview the rows.'
            : 'Step 2 of 2 — map fields onto the chat template, then save.'}
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-rose-950/50 px-3 py-2 font-mono text-xs text-rose-300">
          {error}
        </div>
      )}

      {step === 1 && (
        <section className="space-y-5">
          <div className="grid grid-cols-4 gap-2">
            {(['upload', 'url', 'scrape', 's3'] as SourceType[]).map((t) => (
              <button
                key={t}
                onClick={() => setSource(t)}
                className={`rounded-md border px-3 py-2 text-sm font-medium ${
                  source === t
                    ? 'border-emerald-500 bg-emerald-950/40 text-emerald-300'
                    : 'border-zinc-800 bg-zinc-900/40 text-zinc-400 hover:border-zinc-700'
                }`}
              >
                {t === 'upload' && '📁 Upload file'}
                {t === 'url' && '🔗 URL'}
                {t === 'scrape' && '🌐 Web scrape'}
                {t === 's3' && '☁ S3 bucket'}
              </button>
            ))}
          </div>

          {source === 'upload' && (
            <Field label="File (.jsonl / .ndjson / .csv / .json)">
              <input
                type="file"
                accept=".jsonl,.ndjson,.csv,.json"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-zinc-800 file:px-3 file:py-1 file:text-xs file:text-zinc-300"
              />
              {file && (
                <p className="mt-1 font-mono text-xs text-zinc-500">
                  {file.name} · {(file.size / 1024).toFixed(1)} KB
                </p>
              )}
            </Field>
          )}

          {source === 'url' && (
            <Field label="Direct file URL (must be jsonl/csv/json)">
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/dataset.jsonl"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              />
            </Field>
          )}

          {source === 'scrape' && (
            <Field label="Web page URL (static HTML extraction via trafilatura)">
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/article"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              />
              <p className="mt-1 text-xs text-zinc-500">
                JS-heavy SPAs won't work. For those, save the rendered page and upload it as HTML.
              </p>
            </Field>
          )}

          {source === 's3' && (
            <>
              <Field label="S3 path">
                <input
                  type="text"
                  value={s3Path}
                  onChange={(e) => setS3Path(e.target.value)}
                  placeholder="s3://my-bucket/path/to/file.jsonl"
                  className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
                />
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="AWS access key (optional — uses env if blank)">
                  <input
                    type="text"
                    value={s3Key}
                    onChange={(e) => setS3Key(e.target.value)}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
                  />
                </Field>
                <Field label="AWS secret key">
                  <input
                    type="password"
                    value={s3Secret}
                    onChange={(e) => setS3Secret(e.target.value)}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
                  />
                </Field>
                <Field label="Region">
                  <input
                    type="text"
                    value={s3Region}
                    onChange={(e) => setS3Region(e.target.value)}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
                  />
                </Field>
              </div>
              <p className="font-mono text-xs text-zinc-500">
                Credentials are sent to the local API and used in-memory only. Not stored.
              </p>
            </>
          )}

          <button
            onClick={doPreview}
            disabled={busy}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
          >
            {busy ? 'Fetching…' : 'Preview rows →'}
          </button>
        </section>
      )}

      {step === 2 && preview && (
        <section className="space-y-5">
          {/* Preview summary */}
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
            <div className="flex items-baseline justify-between">
              <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
                Preview
              </h3>
              <div className="font-mono text-xs text-zinc-500">
                {preview.total_rows} row{preview.total_rows === 1 ? '' : 's'} · {preview.format} · {preview.source_type}
              </div>
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full font-mono text-xs">
                <thead className="text-zinc-500">
                  <tr>
                    {preview.detected_fields.map((f) => (
                      <th key={f} className="px-2 py-1.5 text-left font-medium">
                        {f}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-zinc-300">
                  {preview.sample_rows.map((row, i) => (
                    <tr key={i} className="border-t border-zinc-800">
                      {preview.detected_fields.map((f) => (
                        <td key={f} className="max-w-xs truncate px-2 py-1.5">
                          {String(row[f] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Schema mapping */}
          <Field label="Dataset name (lowercase, hyphens/underscores; will be the folder name)">
            <input
              type="text"
              value={datasetName}
              onChange={(e) => setDatasetName(e.target.value)}
              placeholder="my-domain-qa"
              pattern="^[a-z0-9][a-z0-9-_]*$"
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Prompt field">
              <select
                value={promptField}
                onChange={(e) => setPromptField(e.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              >
                {preview.detected_fields.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Response field">
              <select
                value={responseField}
                onChange={(e) => setResponseField(e.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              >
                {preview.detected_fields.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Chat template (match this to your target base model family)">
            <select
              value={template}
              onChange={(e) => setTemplate(e.target.value as Template)}
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            >
              <option value="qwen">Qwen (default)</option>
              <option value="llama3">Llama 3</option>
              <option value="gemma">Gemma</option>
              <option value="raw">Raw (no template)</option>
            </select>
          </Field>

          <Field label="System prompt (optional — prepended to every example)">
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={2}
              placeholder="e.g. You are a helpful stock analyst."
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Validation fraction">
              <input
                type="number"
                value={validFraction}
                onChange={(e) => setValidFraction(parseFloat(e.target.value))}
                min={0}
                max={0.5}
                step={0.01}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              />
            </Field>
            <Field label="Canary fraction (held-out for Goodhart check)">
              <input
                type="number"
                value={canaryFraction}
                onChange={(e) => setCanaryFraction(parseFloat(e.target.value))}
                min={0}
                max={0.3}
                step={0.01}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
              />
            </Field>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setStep(1)}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
            >
              ← Back
            </button>
            <button
              onClick={doFinalize}
              disabled={busy || !datasetName}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
            >
              {busy ? 'Saving…' : `Save dataset (${preview.total_rows} rows)`}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  14. UPDATE Nav.tsx — add "+ New Dataset" link                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
cat > apps/web/src/components/Nav.tsx <<'EOF'
import { NavLink } from 'react-router-dom';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
const activeLink = 'bg-zinc-800 text-zinc-100';

export default function Nav() {
  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/sessions" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Sessions
            </NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Runs
            </NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Datasets
            </NavLink>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <NavLink
            to="/datasets/new"
            className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
          >
            + Dataset
          </NavLink>
          <NavLink
            to="/sessions/new"
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
          >
            + Session
          </NavLink>
        </div>
      </div>
    </header>
  );
}
EOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  15. Ensure docker-compose mounts the staging dir                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
# data/ is already mounted by docker-compose, so the staging subdir is covered.
# Just need to make sure the .ingest_staging directory exists on host.
mkdir -p data/.ingest_staging
touch data/.ingest_staging/.gitkeep

# Make sure .gitignore knows about it
if ! grep -q "ingest_staging" .gitignore 2>/dev/null; then
    cat >> .gitignore <<'EOF'

# Phase 3 ingest staging (temp files between preview and finalize)
data/.ingest_staging/*
!data/.ingest_staging/.gitkeep
EOF
fi

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 3 patch applied                                             ║
╚══════════════════════════════════════════════════════════════════════╝

What's new:
  • packages/ingest/      — formatter, local, url, scrape, s3, staging
  • apps/api/routers/     — ingest.py (5 endpoints)
  • apps/web/src/pages/   — NewDataset.tsx (2-step wizard)
  • UI updates            — /datasets/new route, "+ Dataset" nav button
  • Hermes skill          — ingest_dataset.md
  • Persistent staging    — survives API restarts, no data loss

Next steps:

  # Rebuild API container (new router + new ingest dependencies)
  make rebuild

  make dev               # T1: UI + API
  make trainer           # T2 (still running): training worker
  # (T3 ratchet only needed for sessions, not for dataset ingestion)

  Then: http://localhost:5173/datasets/new

Test paths to try:

  1. Upload: drag any .jsonl or .csv file. The wizard previews 5 rows,
     you map fields → click Save → it lands in /data/datasets/<name>/

  2. URL: try a HuggingFace dataset file:
     https://huggingface.co/datasets/databricks/databricks-dolly-15k/resolve/main/databricks-dolly-15k.jsonl
     (note: the redirect requires HTTPS — should work)

  3. Scrape: any static blog post URL (try a Wikipedia article).
     Returns one row with {url, title, content} fields.

  4. S3: needs valid creds. The form has access_key, secret_key, region.
     Pasted creds are used in-memory only — not stored.

If you hit issues, paste the API container log (docker compose logs api -f)
and the browser console error.
MSG
