"""Unified Jobs API — one composite-id lookup over every long-running thing.

The UI references a "Jobs tab" in several error paths (e.g.
``SynthesizeModal``: "Stream closed unexpectedly. The job may still be
running — check Jobs tab.") but no such tab existed prior to this
router. The page now lives at ``/jobs`` and lets users paste a
composite id to see status, recent log, parent links, and error state.

Composite id format: ``<kind>:<id>`` where kind is one of:

  ``run``      → ``int``  ─ Run row + recent metrics
  ``session``  → ``int``  ─ TrainingSession + child runs
  ``export``   → ``int``  ─ Export row
  ``ingest``   → ``int``  ─ IngestJob row (large-dataset upload)
  ``autofix``  → ``int``  ─ AutoFixAttempt row
  ``agent``    → ``hex``  ─ hermes_traces by ``agent_run_id`` (Phase B)
  ``synth``    → ``hex``  ─ in-memory synth job (apps/api/routers/synth.py)
  ``research`` → ``hex``  ─ in-memory research job

Tenant isolation: all DB-backed lookups go through ``scope_query`` so
cross-tenant access returns 404, not 403 — surfacing the existence of
another tenant's job by differentiating the codes would leak metadata.
In-memory jobs (synth/research) inherit the running process's tenant
context via the synth/research routers themselves; this aggregator
does not re-scope them.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.middleware.auth import requires
from apps.api.models.autofix import AutoFixAttempt
from apps.api.models.export import Export
from apps.api.models.hermes_trace import HermesTrace
from apps.api.models.ingest_job import IngestJob
from apps.api.models.model_download_job import ModelDownloadJob
from apps.api.models.run import Run
from apps.api.models.session import TrainingSession
from apps.api.services.db import get_session
from apps.api.services.identity import Identity, current_identity
from apps.api.services.scoping import scope_query

log = logging.getLogger("api.jobs")
router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


JobKind = Literal[
    "run", "session", "export", "autofix", "agent", "synth", "research",
    "ingest", "modeldownload",
]
_VALID_KINDS: set[str] = {
    "run", "session", "export", "autofix", "agent", "synth", "research",
    "ingest", "modeldownload",
}


class JobDetail(BaseModel):
    """Uniform shape returned by ``GET /api/v1/jobs/{job_id}``.

    The frontend doesn't switch on ``kind`` for layout — it renders the
    common fields and lists ``links`` for kind-specific deep links
    (e.g. the Run detail page, the Traces tab filtered to this trace).
    ``progress`` and ``summary`` are free-form dicts so each kind can
    surface its own metrics without adding columns to this schema.
    """

    job_id: str
    kind: JobKind
    status: str
    parent_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    summary: str | None = None
    progress: dict[str, Any] | None = None
    links: dict[str, str] = {}


def _parse_id(job_id: str) -> tuple[str, str]:
    """Split ``<kind>:<id>``; raise 400 on malformed input."""
    if ":" not in job_id:
        raise HTTPException(
            400,
            "job_id must be '<kind>:<id>' — e.g. 'run:42', 'agent:abc123', "
            "'synth:def456'.",
        )
    kind, _, rest = job_id.partition(":")
    if kind not in _VALID_KINDS:
        raise HTTPException(
            400,
            f"unknown kind {kind!r}. Valid kinds: {sorted(_VALID_KINDS)}",
        )
    if not rest:
        raise HTTPException(400, f"missing id after {kind!r}:")
    return kind, rest


# ─── per-kind resolvers ──────────────────────────────────────────────────


def _resolve_run(rid: str, identity: Identity, db: Session) -> JobDetail:
    try:
        run_pk = int(rid)
    except ValueError as e:
        raise HTTPException(400, "run id must be an integer") from e
    rows = list(
        db.exec(scope_query(select(Run), identity, Run).where(Run.id == run_pk)).all()
    )
    if not rows:
        raise HTTPException(404, f"run:{rid} not found")
    r = rows[0]
    return JobDetail(
        job_id=f"run:{r.id}",
        kind="run",
        status=r.status.value if hasattr(r.status, "value") else str(r.status),
        parent_id=str(r.id),
        tenant_id=r.tenant_id,
        user_id=r.user_id,
        started_at=r.started_at.isoformat() if r.started_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        error=getattr(r, "error_message", None),
        summary=f"{r.method.value if hasattr(r.method, 'value') else r.method} on {r.dataset} ({r.base_model})",
        progress={
            "iters": r.iters,
            "trainer_backend": r.trainer_backend,
        },
        links={
            "detail": f"/runs/{r.id}",
            "session": f"/experiments/{r.session_id}" if r.session_id else "",
        },
    )


def _resolve_session(sid: str, identity: Identity, db: Session) -> JobDetail:
    try:
        sess_pk = int(sid)
    except ValueError as e:
        raise HTTPException(400, "session id must be an integer") from e
    rows = list(
        db.exec(
            scope_query(select(TrainingSession), identity, TrainingSession).where(
                TrainingSession.id == sess_pk
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"session:{sid} not found")
    s = rows[0]
    children = list(db.exec(select(Run).where(Run.session_id == sess_pk)).all())
    return JobDetail(
        job_id=f"session:{s.id}",
        kind="session",
        status=s.status.value if hasattr(s.status, "value") else str(s.status),
        parent_id=str(s.id),
        tenant_id=s.tenant_id,
        user_id=s.user_id,
        started_at=s.started_at.isoformat() if s.started_at else None,
        completed_at=s.completed_at.isoformat() if s.completed_at else None,
        error=s.error_message,
        summary=f"{s.name} — {len(children)} run(s), best={s.best_run_id}",
        progress={
            "current_round": s.current_round,
            "max_rounds": s.max_rounds,
        },
        links={"detail": f"/experiments/{s.id}"},
    )


def _resolve_export(xid: str, identity: Identity, db: Session) -> JobDetail:
    try:
        exp_pk = int(xid)
    except ValueError as e:
        raise HTTPException(400, "export id must be an integer") from e
    rows = list(
        db.exec(
            scope_query(select(Export), identity, Export).where(Export.id == exp_pk)
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"export:{xid} not found")
    e = rows[0]
    return JobDetail(
        job_id=f"export:{e.id}",
        kind="export",
        status=e.status.value if hasattr(e.status, "value") else str(e.status),
        parent_id=str(e.id),
        tenant_id=e.tenant_id,
        user_id=e.user_id,
        started_at=e.started_at.isoformat() if e.started_at else None,
        completed_at=e.completed_at.isoformat() if e.completed_at else None,
        error=getattr(e, "error_message", None),
        summary=f"Export of run {e.run_id}",
        progress=None,
        links={"detail": "/exports", "run": f"/runs/{e.run_id}"},
    )


def _resolve_ingest(iid: str, identity: Identity, db: Session) -> JobDetail:
    """Look up a durable background large-dataset ingest job.

    Tenant-scoped via ``scope_query`` (cross-tenant → 404). Progress
    surfaces the byte/record tallies the Jobs tab polls while the job
    runs; the deep link points at the published dataset once succeeded,
    otherwise at the datasets list.
    """
    try:
        job_pk = int(iid)
    except ValueError as e:
        raise HTTPException(400, "ingest id must be an integer") from e
    rows = list(
        db.exec(
            scope_query(select(IngestJob), identity, IngestJob).where(
                IngestJob.id == job_pk
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"ingest:{iid} not found")
    j = rows[0]
    status = j.status.value if hasattr(j.status, "value") else str(j.status)
    detail_link = (
        f"/datasets/{j.dataset_name}" if status == "succeeded" else "/datasets"
    )
    return JobDetail(
        job_id=f"ingest:{j.id}",
        kind="ingest",
        status=status,
        parent_id=str(j.id),
        tenant_id=j.tenant_id,
        user_id=j.user_id,
        started_at=j.started_at.isoformat() if j.started_at else None,
        completed_at=j.completed_at.isoformat() if j.completed_at else None,
        error=j.error_message,
        summary=f"Ingest '{j.dataset_name}' ({j.detected_format})",
        progress={
            "raw_bytes": j.raw_bytes,
            "records_total": j.records_total,
            "train": j.train_count,
            "valid": j.valid_count,
            "canary": j.canary_count,
            "dropped": j.dropped_count,
            "format": j.detected_format,
        },
        links={"detail": detail_link},
    )


def _resolve_model_download(mid: str, identity: Identity, db: Session) -> JobDetail:
    """Look up a HuggingFace model-registration job.

    Tenant-scoped via ``scope_query`` (cross-tenant → 404). Progress surfaces
    the detected metadata; the deep link points at the Models tab (or the
    New Run page once the model is registered and usable).
    """
    try:
        job_pk = int(mid)
    except ValueError as e:
        raise HTTPException(400, "modeldownload id must be an integer") from e
    rows = list(
        db.exec(
            scope_query(select(ModelDownloadJob), identity, ModelDownloadJob).where(
                ModelDownloadJob.id == job_pk
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"modeldownload:{mid} not found")
    j = rows[0]
    status = j.status.value if hasattr(j.status, "value") else str(j.status)
    detail_link = "/runs/new" if status == "succeeded" else "/models"
    return JobDetail(
        job_id=f"modeldownload:{j.id}",
        kind="modeldownload",
        status=status,
        parent_id=str(j.id),
        tenant_id=j.tenant_id,
        user_id=j.user_id,
        started_at=j.started_at.isoformat() if j.started_at else None,
        completed_at=j.completed_at.isoformat() if j.completed_at else None,
        error=j.error_message,
        summary=f"Register '{j.hf_id}' ({j.target_backend})",
        progress={
            "hf_id": j.hf_id,
            "backend": j.target_backend,
            "family": j.detected_family,
            "params": j.detected_params,
            "arch": j.detected_arch,
            "gated": j.gated,
            "registered_key": j.registered_key,
        },
        links={"detail": detail_link, "models": "/models"},
    )


def _resolve_autofix(aid: str, identity: Identity, db: Session) -> JobDetail:
    try:
        af_pk = int(aid)
    except ValueError as e:
        raise HTTPException(400, "autofix id must be an integer") from e
    rows = list(
        db.exec(
            scope_query(select(AutoFixAttempt), identity, AutoFixAttempt).where(
                AutoFixAttempt.id == af_pk
            )
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"autofix:{aid} not found")
    a = rows[0]
    status = a.status.value if hasattr(a.status, "value") else str(a.status)
    return JobDetail(
        job_id=f"autofix:{a.id}",
        kind="autofix",
        status=status,
        parent_id=str(a.id),
        tenant_id=a.tenant_id,
        user_id=a.user_id,
        error=getattr(a, "error_message", None),
        summary=f"AutoFix attempt {a.id}",
        progress=None,
        links={"detail": "/autofix"},
    )


def _resolve_agent(arid: str, identity: Identity, db: Session) -> JobDetail:
    """Look up an agent run by ``hermes_traces.agent_run_id``.

    Returns the parent span as the canonical row. Other spans of the
    same trace are not enumerated here — the Traces tab does that via
    ``GET /api/v1/hermes/traces?agent_run_id=...``.
    """
    rows = list(
        db.exec(
            select(HermesTrace)
            .where(HermesTrace.agent_run_id == arid)
            .where(HermesTrace.kind == "agent")
            .where(HermesTrace.tenant_id == identity.tenant_id)
        ).all()
    )
    if not rows:
        raise HTTPException(404, f"agent:{arid} not found")
    a = rows[0]
    status = (
        "running"
        if a.duration_ms == 0 and a.error is None
        else ("failed" if a.error else "completed")
    )
    return JobDetail(
        job_id=f"agent:{arid}",
        kind="agent",
        status=status,
        parent_id=arid,
        tenant_id=a.tenant_id,
        user_id=None,
        started_at=a.created_at.isoformat() if a.created_at else None,
        error=a.error,
        summary=f"Agent: {a.source}",
        progress=None,
        links={"trace": f"/traces?agent_run_id={arid}"},
    )


def _resolve_synth(sid: str, identity: Identity, db: Session) -> JobDetail:
    """In-memory synth job lookup — federates over
    ``apps.api.routers.synth._JOBS``. Returns 404 once the job has
    aged out of the registry (synth jobs are best-effort by design)."""
    # Lazy import to break the otherwise circular import between
    # synth.py and jobs.py via FastAPI router registration.
    from apps.api.routers import synth as synth_router

    job = synth_router._JOBS.get(sid)
    if job is None:
        raise HTTPException(404, f"synth:{sid} not found in active registry")
    snap = job.snapshot().model_dump()
    return JobDetail(
        job_id=f"synth:{sid}",
        kind="synth",
        status=snap["status"],
        parent_id=sid,
        tenant_id=identity.tenant_id,
        started_at=snap.get("created_at"),
        completed_at=snap.get("completed_at"),
        error=snap.get("error"),
        summary=f"Synth {snap.get('source_dataset')} → {snap.get('new_dataset')}",
        progress={
            "generated": snap.get("generated"),
            "target": snap.get("target_count"),
            "batch": snap.get("batch"),
            "dropped": snap.get("dropped_total"),
        },
        links={
            "stream": f"/api/v1/synth/jobs/{sid}/stream",
            "datasets": "/datasets",
        },
    )


def _resolve_research(rid: str, identity: Identity, db: Session) -> JobDetail:
    """In-memory research job lookup — same shape as synth."""
    from apps.api.routers import research as research_router

    job = research_router._JOBS.get(rid) if hasattr(research_router, "_JOBS") else None
    if job is None:
        raise HTTPException(
            404, f"research:{rid} not found in active registry"
        )
    snap = job.snapshot().model_dump() if hasattr(job, "snapshot") else {}
    return JobDetail(
        job_id=f"research:{rid}",
        kind="research",
        status=snap.get("status", "unknown"),
        parent_id=rid,
        tenant_id=identity.tenant_id,
        started_at=snap.get("created_at"),
        completed_at=snap.get("completed_at"),
        error=snap.get("error"),
        summary=f"Research: {snap.get('topic', rid)}",
        progress={
            "phase": snap.get("phase"),
            "step": snap.get("step"),
        },
        links={
            "stream": f"/api/v1/research/jobs/{rid}/stream",
            "reports": "/research",
        },
    )


_RESOLVERS = {
    "run": _resolve_run,
    "session": _resolve_session,
    "export": _resolve_export,
    "ingest": _resolve_ingest,
    "modeldownload": _resolve_model_download,
    "autofix": _resolve_autofix,
    "agent": _resolve_agent,
    "synth": _resolve_synth,
    "research": _resolve_research,
}


# ─── endpoint ────────────────────────────────────────────────────────────


@router.get("/{job_id:path}", response_model=JobDetail)
@requires("read", "run")  # any role with run:read can query jobs they own
def get_job(
    job_id: str,
    request: Request,
    db: SessionDep,
) -> JobDetail:
    """Resolve a composite job id into a uniform :class:`JobDetail`.

    Tenant isolation is enforced by ``scope_query`` for DB-backed kinds;
    in-memory kinds inherit the caller's tenant from ``Identity``.
    Cross-tenant access returns 404 (not 403) so metadata about other
    tenants' jobs doesn't leak via status codes.
    """
    kind, rest = _parse_id(job_id)
    identity = current_identity(request)
    resolver = _RESOLVERS[kind]
    return resolver(rest, identity, db)