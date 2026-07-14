"""Phase C.5 — Unified ``/api/v1/jobs/{job_id}`` federated lookup.

The UI references a "Jobs tab" from several error paths (e.g.
``SynthesizeModal``: "Stream closed unexpectedly. The job may still be
running — check Jobs tab.") but no such tab existed. This router
exposes a single composite-id lookup that surfaces:

  synth:<hex>    — in-memory synth job (apps/api/routers/synth.py)
  research:<hex> — in-memory research job (apps/api/routers/research.py)
  run:<int>      — Run row + recent metrics
  session:<int>  — TrainingSession + child runs
  export:<int>   — Export row
  autofix:<int>  — AutoFixAttempt row
  agent:<hex>    — hermes_traces by ``agent_run_id`` (Phase B)

Each returns a uniform ``JobDetail`` shape so the frontend doesn't
have to switch on kind.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.middleware import auth as auth_module
from apps.api.middleware.auth import User
from apps.api.models.export import Export, ExportStatus
from apps.api.models.hermes_trace import HermesTrace
from apps.api.models.ingest_job import IngestJob, IngestStatus
from apps.api.models.run import Run, RunMethod, RunStatus
from apps.api.models.session import TrainingSession
from apps.api.routers.jobs import get_job
from apps.api.services import auth_settings as auth_settings_module
from apps.api.services import db as db_module


@pytest.fixture()
def engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    auth_settings_module.get_auth_settings.cache_clear()
    monkeypatch.setattr(
        auth_module,
        "policy_check",
        lambda user, action, resource, settings=None: (True, ""),
    )
    yield eng
    eng.dispose()
    auth_settings_module.get_auth_settings.cache_clear()


def _req(user: User) -> MagicMock:
    req = MagicMock()
    req.state.user = user
    return req


def _alice() -> User:
    return User(id="alice", email="alice@x", roles=["admin"], groups=["/tenants/acme"])


def _seed_run(eng) -> int:
    with Session(eng) as s:
        r = Run(
            dataset="d", base_model="m", method=RunMethod.LORA, iters=10,
            batch_size=1, learning_rate=1e-4, num_layers=2, max_seq_length=512,
            grad_checkpoint=False, seed=0, status=RunStatus.RUNNING,
            tenant_id="acme", user_id="alice", role="admin",
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id or 0


def _seed_export(eng) -> int:
    with Session(eng) as s:
        e = Export(
            run_id=1,
            base_model="m",
            method="lora",
            quant_levels="Q4_K_M",
            status=ExportStatus.FUSING,
            tenant_id="acme",
            user_id="alice",
            role="admin",
        )
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id or 0


def _seed_agent_trace(eng) -> str:
    agent_run_id = "agent-test-1"
    with Session(eng) as s:
        s.add(
            HermesTrace(
                source="experiment_recommender",
                model="hermes",
                request_body="{}",
                response_body="{}",
                duration_ms=100,
                kind="agent",
                trace_id="t1",
                span_id="s1",
                parent_span_id=None,
                agent_run_id=agent_run_id,
                tenant_id="acme",
                created_at=datetime.now(UTC),
            )
        )
        s.commit()
    return agent_run_id


def _seed_ingest(
    eng,
    *,
    status: IngestStatus = IngestStatus.SUCCEEDED,
    tenant_id: str = "acme",
    name: str = "bigcorpus",
) -> int:
    with Session(eng) as s:
        job = IngestJob(
            tenant_id=tenant_id,
            user_id="alice",
            dataset_name=name,
            status=status,
            source_filename="corpus.jsonl",
            detected_format="jsonl_chat",
            raw_key=f"{tenant_id}/admin/alice/data/ingest-x/raw.jsonl",
            raw_bytes=4096,
            records_total=20,
            train_count=15,
            valid_count=4,
            canary_count=1,
            dropped_count=0,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id or 0


def test_get_job_ingest_kind_returns_ingest_detail(engine):
    jid = _seed_ingest(engine, status=IngestStatus.SUCCEEDED)
    with Session(engine) as db:
        detail = get_job(job_id=f"ingest:{jid}", request=_req(_alice()), db=db)
    assert detail.kind == "ingest"
    assert detail.job_id == f"ingest:{jid}"
    assert detail.status == "succeeded"
    assert detail.parent_id == str(jid)
    assert detail.progress is not None
    assert detail.progress["records_total"] == 20
    assert detail.progress["train"] == 15
    assert detail.progress["valid"] == 4
    assert detail.progress["canary"] == 1
    assert detail.progress["dropped"] == 0
    assert detail.progress["raw_bytes"] == 4096
    assert detail.progress["format"] == "jsonl_chat"
    # Succeeded → deep link to the published dataset.
    assert detail.links["detail"] == "/datasets/bigcorpus"


def test_get_job_ingest_queued_links_to_datasets(engine):
    jid = _seed_ingest(engine, status=IngestStatus.QUEUED)
    with Session(engine) as db:
        detail = get_job(job_id=f"ingest:{jid}", request=_req(_alice()), db=db)
    assert detail.status == "queued"
    # Not yet published → link to the datasets list, not a specific dataset.
    assert detail.links["detail"] == "/datasets"


def test_get_job_ingest_unknown_id_returns_404(engine):
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id="ingest:99999", request=_req(_alice()), db=db)
    assert ei.value.status_code == 404


def test_get_job_ingest_non_integer_id_returns_400(engine):
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id="ingest:abc", request=_req(_alice()), db=db)
    assert ei.value.status_code == 400


def test_get_job_ingest_cross_tenant_is_404(engine):
    # Row belongs to tenant "other"; alice (tenant acme) must not see it.
    jid = _seed_ingest(engine, tenant_id="other")
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id=f"ingest:{jid}", request=_req(_alice()), db=db)
    assert ei.value.status_code == 404


def test_get_job_run_kind_returns_run_detail(engine):
    rid = _seed_run(engine)
    with Session(engine) as db:
        detail = get_job(job_id=f"run:{rid}", request=_req(_alice()), db=db)
    assert detail.kind == "run"
    assert detail.job_id == f"run:{rid}"
    assert detail.status == "running"
    assert detail.parent_id == str(rid)


def test_get_job_export_kind_returns_export_detail(engine):
    xid = _seed_export(engine)
    with Session(engine) as db:
        detail = get_job(job_id=f"export:{xid}", request=_req(_alice()), db=db)
    assert detail.kind == "export"
    assert detail.status == "fusing"


def test_get_job_agent_kind_returns_agent_detail(engine):
    arid = _seed_agent_trace(engine)
    with Session(engine) as db:
        detail = get_job(job_id=f"agent:{arid}", request=_req(_alice()), db=db)
    assert detail.kind == "agent"
    assert detail.status in {"running", "completed"}
    assert detail.summary  # carries the agent name


def test_get_job_unknown_kind_returns_400(engine):
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id="unicorn:abc", request=_req(_alice()), db=db)
    assert ei.value.status_code == 400


def test_get_job_unparseable_id_returns_400(engine):
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id="no-colon-here", request=_req(_alice()), db=db)
    assert ei.value.status_code == 400


def test_get_job_run_not_found_returns_404(engine):
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id="run:99999", request=_req(_alice()), db=db)
    assert ei.value.status_code == 404


def test_get_job_cross_tenant_is_404_not_403(engine):
    """Phase C — surfacing the existence of another tenant's job by
    differentiating 403 from 404 would leak metadata. Either return
    404 (preferred) or scope_query the lookup so cross-tenant rows
    simply don't surface."""
    rid = _seed_run(engine)
    bob = User(id="bob", email="bob@globex", roles=["admin"], groups=["/tenants/globex"])
    with Session(engine) as db, pytest.raises(HTTPException) as ei:
        get_job(job_id=f"run:{rid}", request=_req(bob), db=db)
    assert ei.value.status_code == 404


def test_get_job_synth_kind_uses_in_memory_registry(engine, monkeypatch):
    """Synth jobs aren't persisted; the unified router federates over
    the synth module's in-memory dict."""
    from apps.api.routers import synth as synth_router

    class _FakeJob:
        job_id = "synthhex123"
        status = "running"

        def snapshot(self):
            class _Snap:
                model_dump = lambda self: {  # noqa: E731
                    "job_id": "synthhex123",
                    "status": "running",
                    "source_dataset": "src",
                    "new_dataset": "dst",
                    "target_count": 100,
                    "generated": 25,
                    "batch": 1,
                    "dropped_total": 0,
                    "created_at": "2026-06-29T00:00:00",
                    "completed_at": None,
                    "error": None,
                    "result": None,
                }
            return _Snap()

    monkeypatch.setitem(synth_router._JOBS, "synthhex123", _FakeJob())

    with Session(engine) as db:
        detail = get_job(
            job_id="synth:synthhex123", request=_req(_alice()), db=db
        )
    assert detail.kind == "synth"
    assert detail.status == "running"
    assert detail.progress is not None
    assert detail.progress.get("generated") == 25