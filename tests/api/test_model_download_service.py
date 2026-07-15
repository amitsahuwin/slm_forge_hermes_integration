"""Model-download (register + validate) background service.

Covers the pure detection helpers, the HF metadata fetch (success / not-found /
gated / transient-retry), the end-to-end job runner driving a row to a terminal
state, and the startup reconciler for orphaned rows.

``HfApi`` is always mocked — no network is touched.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.model_download_job import ModelDownloadJob, ModelDownloadStatus
from apps.api.models.registered_model import RegisteredModel
from apps.api.services import db as db_module
from apps.api.services import model_download_jobs as svc


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    eng = create_engine(f"sqlite:///{tmp_path / 'downloads.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    yield
    eng.dispose()


def _new_job(hf_id: str, backend: str = "cuda", tenant: str = "acme") -> int:
    with Session(db_module.engine) as s:
        job = ModelDownloadJob(
            tenant_id=tenant,
            user_id="alice",
            hf_id=hf_id,
            target_backend=backend,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        assert job.id is not None
        return job.id


# --------------------------------------------------------------------------- #
# Fake HF Hub types
# --------------------------------------------------------------------------- #
@dataclass
class _FakeSafetensors:
    total: int | None


class _FakeInfo:
    def __init__(
        self,
        gated: bool = False,
        total: int | None = 1_500_000_000,
        arch: str | None = "Qwen2ForCausalLM",
    ) -> None:
        self.gated = gated
        self.safetensors = _FakeSafetensors(total) if total is not None else None
        self.config = {"architectures": [arch]} if arch else {}


class _FakeApi:
    """Stand-in for ``HfApi`` whose ``model_info`` returns/raises on cue."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls = 0

    def model_info(self, hf_id: str, token: str | None = None) -> object:
        self.calls += 1
        res = self._result
        if isinstance(res, list):
            res = res[min(self.calls - 1, len(res) - 1)]
        if isinstance(res, Exception):
            raise res
        return res


class _FakeResponse:
    """Minimal ``requests.Response`` stand-in for HF exception constructors."""

    status_code = 503
    headers: ClassVar[dict[str, str]] = {}
    request = None


def _hf_error(cls: type[Exception], message: str) -> Exception:
    return cls(message, response=_FakeResponse())  # type: ignore[call-arg]


def _install_api(monkeypatch: pytest.MonkeyPatch, result: object) -> _FakeApi:
    import huggingface_hub

    fake = _FakeApi(result)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: fake)
    # Neutralize backoff sleeps so retry tests stay fast.
    monkeypatch.setattr(svc.time, "sleep", lambda *_a, **_k: None)
    return fake


# --------------------------------------------------------------------------- #
# Pure detection helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hf_id",
    [
        "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "someorg/model-mlx",
        "someorg/model-8bit",
    ],
)
def test_infer_backend_explicit_mlx_repos_force_mlx(
    hf_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Explicit MLX builds resolve to mlx regardless of host platform.
    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "linux")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "true")
    assert svc.infer_backend(hf_id) == "mlx"


@pytest.mark.parametrize(
    "hf_id",
    ["Qwen/Qwen2.5-1.5B-Instruct", "meta-llama/Llama-3.2-1B-Instruct"],
)
def test_infer_backend_generic_repo_follows_host_default(
    hf_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A generic repo works on both backends → follow the host's default.
    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "Darwin")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_ARCH", "arm64")
    monkeypatch.delenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", raising=False)
    assert svc.infer_backend(hf_id) == "mlx"

    monkeypatch.setenv("SLM_FORGE_PLATFORM_OS", "linux")
    monkeypatch.setenv("SLM_FORGE_PLATFORM_HAS_NVIDIA", "true")
    assert svc.infer_backend(hf_id) == "cuda"


def test_human_params() -> None:
    assert svc._human_params(None) == "unknown"
    assert svc._human_params(0) == "unknown"
    assert svc._human_params(1_500_000_000) == "1.5B"
    assert svc._human_params(3_000_000_000) == "3B"
    assert svc._human_params(500_000_000) == "500M"


def test_infer_family() -> None:
    assert svc._infer_family("Qwen/Qwen2.5-1.5B-Instruct", None) == "qwen"
    assert svc._infer_family("meta-llama/Llama-3.2-1B", None) == "llama"
    assert svc._infer_family("org/mystery", "GemmaForCausalLM") == "gemma"


def test_slug_and_unique_key() -> None:
    assert svc._slug("Qwen/Qwen2.5-1.5B-Instruct") == "qwen2-5-1-5b-instruct"
    taken = {"qwen", "qwen-2"}
    assert svc._unique_key("fresh", taken) == "fresh"
    assert svc._unique_key("qwen", taken) == "qwen-3"


def test_min_memory_gb() -> None:
    assert svc._min_memory_gb(None, "cuda") == 0.0
    assert svc._min_memory_gb(3_000_000_000, "mlx") > 0
    # cuda has more overhead than mlx for the same size.
    assert svc._min_memory_gb(3_000_000_000, "cuda") > svc._min_memory_gb(
        3_000_000_000, "mlx"
    )


# --------------------------------------------------------------------------- #
# _fetch_model_meta
# --------------------------------------------------------------------------- #
def test_fetch_meta_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _install_api(monkeypatch, _FakeInfo(gated=False, total=1_500_000_000))
    meta = svc._fetch_model_meta("Qwen/Qwen2.5-1.5B-Instruct")
    assert meta.gated is False
    assert meta.params_total == 1_500_000_000
    assert meta.arch == "Qwen2ForCausalLM"


def test_fetch_meta_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from huggingface_hub.utils import RepositoryNotFoundError

    _install_api(monkeypatch, _hf_error(RepositoryNotFoundError, "nope"))
    with pytest.raises(svc.ModelDownloadError, match="not found"):
        svc._fetch_model_meta("nobody/nothing")


def test_fetch_meta_gated_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from huggingface_hub.utils import GatedRepoError

    _install_api(monkeypatch, _hf_error(GatedRepoError, "gated"))
    with pytest.raises(svc.ModelDownloadError, match="gated"):
        svc._fetch_model_meta("meta-llama/Llama-3.2-1B-Instruct")


def test_fetch_meta_gated_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _install_api(monkeypatch, _FakeInfo(gated=True))
    with pytest.raises(svc.ModelDownloadError, match="gated"):
        svc._fetch_model_meta("meta-llama/Llama-3.2-1B-Instruct")


def test_fetch_meta_gated_with_token_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "<HF_TOKEN>")
    _install_api(monkeypatch, _FakeInfo(gated=True))
    meta = svc._fetch_model_meta("meta-llama/Llama-3.2-1B-Instruct")
    assert meta.gated is True


def test_fetch_meta_retries_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    from huggingface_hub.utils import HfHubHTTPError

    monkeypatch.delenv("HF_TOKEN", raising=False)
    fake = _install_api(
        monkeypatch, [_hf_error(HfHubHTTPError, "503"), _FakeInfo(gated=False)]
    )
    meta = svc._fetch_model_meta("Qwen/Qwen2.5-1.5B-Instruct")
    assert meta.gated is False
    assert fake.calls == 2


def test_fetch_meta_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from huggingface_hub.utils import HfHubHTTPError

    monkeypatch.delenv("HF_TOKEN", raising=False)
    fake = _install_api(monkeypatch, _hf_error(HfHubHTTPError, "503"))
    with pytest.raises(svc.ModelDownloadError, match="Could not reach"):
        svc._fetch_model_meta("Qwen/Qwen2.5-1.5B-Instruct")
    assert fake.calls == svc._MAX_ATTEMPTS


# --------------------------------------------------------------------------- #
# End-to-end job runner (patches _fetch_model_meta directly)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_success_registers_model(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _new_job("Qwen/Qwen2.5-1.5B-Instruct", backend="cuda")
    monkeypatch.setattr(
        svc,
        "_fetch_model_meta",
        lambda _hf: svc._ModelMeta(gated=False, params_total=1_500_000_000, arch="Qwen2ForCausalLM"),
    )
    await svc._run_model_download_job(job_id)

    with Session(db_module.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        assert job is not None
        assert job.status == ModelDownloadStatus.SUCCEEDED
        assert job.registered_key
        assert job.detected_family == "qwen"
        assert job.detected_params == "1.5B"
        assert job.completed_at is not None

        row = s.exec(
            select(RegisteredModel).where(
                RegisteredModel.model_id == "Qwen/Qwen2.5-1.5B-Instruct"
            )
        ).first()
        assert row is not None
        assert row.backend == "cuda"
        assert row.key == job.registered_key
        assert row.created_by_tenant_id == "acme"


@pytest.mark.asyncio
async def test_run_honors_backend_override(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An id that auto-detects mlx, but the job requests cuda → row is cuda.
    job_id = _new_job("someorg/model-mlx", backend="cuda")
    monkeypatch.setattr(
        svc,
        "_fetch_model_meta",
        lambda _hf: svc._ModelMeta(gated=False, params_total=800_000_000, arch=None),
    )
    await svc._run_model_download_job(job_id)

    with Session(db_module.engine) as s:
        row = s.exec(
            select(RegisteredModel).where(
                RegisteredModel.model_id == "someorg/model-mlx"
            )
        ).first()
        assert row is not None
        assert row.backend == "cuda"


@pytest.mark.asyncio
async def test_run_missing_repo_fails(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _new_job("nobody/nothing")

    def _boom(_hf: str) -> svc._ModelMeta:
        raise svc.ModelDownloadError("'nobody/nothing' was not found on HuggingFace")

    monkeypatch.setattr(svc, "_fetch_model_meta", _boom)
    await svc._run_model_download_job(job_id)

    with Session(db_module.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        assert job is not None
        assert job.status == ModelDownloadStatus.FAILED
        assert "not found" in (job.error_message or "")
        assert (
            s.exec(select(RegisteredModel)).first() is None
        ), "no registry row on failure"


@pytest.mark.asyncio
async def test_run_gated_without_token_fails(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _new_job("meta-llama/Llama-3.2-1B-Instruct")

    def _gated(_hf: str) -> svc._ModelMeta:
        raise svc.ModelDownloadError("is gated on HuggingFace; set a valid HF_TOKEN")

    monkeypatch.setattr(svc, "_fetch_model_meta", _gated)
    await svc._run_model_download_job(job_id)

    with Session(db_module.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        assert job is not None
        assert job.status == ModelDownloadStatus.FAILED
        assert "HF_TOKEN" in (job.error_message or "")


@pytest.mark.asyncio
async def test_run_unexpected_error_fails(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _new_job("Qwen/Qwen2.5-1.5B-Instruct")

    def _kaboom(_hf: str) -> svc._ModelMeta:
        raise RuntimeError("segfault in the matrix")

    monkeypatch.setattr(svc, "_fetch_model_meta", _kaboom)
    await svc._run_model_download_job(job_id)

    with Session(db_module.engine) as s:
        job = s.get(ModelDownloadJob, job_id)
        assert job is not None
        assert job.status == ModelDownloadStatus.FAILED
        assert "unexpected error" in (job.error_message or "")


@pytest.mark.asyncio
async def test_run_missing_row_is_noop(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def _spy(_hf: str) -> svc._ModelMeta:
        nonlocal called
        called = True
        return svc._ModelMeta(gated=False, params_total=1, arch=None)

    monkeypatch.setattr(svc, "_fetch_model_meta", _spy)
    await svc._run_model_download_job(99999)  # no such row
    assert called is False


@pytest.mark.asyncio
async def test_upsert_is_idempotent(
    db_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc,
        "_fetch_model_meta",
        lambda _hf: svc._ModelMeta(gated=False, params_total=1_500_000_000, arch="Qwen2ForCausalLM"),
    )
    first = _new_job("Qwen/Qwen2.5-1.5B-Instruct", backend="cuda")
    await svc._run_model_download_job(first)
    second = _new_job("Qwen/Qwen2.5-1.5B-Instruct", backend="mlx")
    await svc._run_model_download_job(second)

    with Session(db_module.engine) as s:
        rows = s.exec(
            select(RegisteredModel).where(
                RegisteredModel.model_id == "Qwen/Qwen2.5-1.5B-Instruct"
            )
        ).all()
        assert len(rows) == 1, "second registration updates, not duplicates"
        assert rows[0].backend == "mlx"  # updated to the latest job's backend


# --------------------------------------------------------------------------- #
# Startup reconciler
# --------------------------------------------------------------------------- #
def test_reconcile_orphans(db_engine: None) -> None:
    queued = _new_job("a/queued")
    processing = _new_job("a/processing")
    with Session(db_module.engine) as s:
        s.get(ModelDownloadJob, processing).status = ModelDownloadStatus.PROCESSING  # type: ignore[union-attr]
        done = ModelDownloadJob(
            tenant_id="acme", user_id="alice", hf_id="a/done",
            target_backend="cuda", status=ModelDownloadStatus.SUCCEEDED,
        )
        s.add(s.get(ModelDownloadJob, processing))
        s.add(done)
        s.commit()
        done_id = done.id

    db_module._reconcile_orphaned_model_download_jobs()

    with Session(db_module.engine) as s:
        assert s.get(ModelDownloadJob, queued).status == ModelDownloadStatus.FAILED  # type: ignore[union-attr]
        assert s.get(ModelDownloadJob, processing).status == ModelDownloadStatus.FAILED  # type: ignore[union-attr]
        # terminal rows are never touched
        assert s.get(ModelDownloadJob, done_id).status == ModelDownloadStatus.SUCCEEDED  # type: ignore[union-attr]
        assert "restart" in (s.get(ModelDownloadJob, queued).error_message or "")  # type: ignore[union-attr]