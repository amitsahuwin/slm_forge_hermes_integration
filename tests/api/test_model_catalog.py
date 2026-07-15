"""Phase P / A1, A3 — catalog v2 integrity + validation rules."""
from __future__ import annotations

import pytest

from apps.api.services import model_catalog as mc

# ---------------------------------------------------------------------------
# A1 — catalog integrity
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {
    "qwen2.5-3b-instruct",
    "llama-3.2-3b-instruct",
    "qwen2.5-7b-instruct",
    "gemma-3-4b-it",
    "gemma-3-12b-it",
    "gemma-4-e2b-it",
    "gemma-4-e4b-it",
    "mistral-7b-instruct-v0.3",
    "gemma-3n-e2b-it",
}


def test_catalog_contains_expected_keys() -> None:
    keys = [m.key for m in mc.CATALOG_V2]
    assert len(keys) == len(set(keys)), "duplicate keys"
    assert set(keys) == EXPECTED_KEYS


def test_every_entry_has_mlx_and_cuda_variants() -> None:
    for m in mc.CATALOG_V2:
        assert "mlx" in m.backends, f"{m.key} missing mlx variant"
        assert "cuda" in m.backends, f"{m.key} missing cuda variant (Phase Q readiness)"
        for variant in m.backends.values():
            assert variant.model_id
            assert variant.min_memory_gb > 0
            assert variant.status in {"stable", "untested", "broken"}


def test_mlx_model_ids_are_unique() -> None:
    ids = [m.backends["mlx"].model_id for m in mc.CATALOG_V2]
    assert len(ids) == len(set(ids))


def test_default_model_is_stable_mlx() -> None:
    default = mc.get_model_by_key(mc.DEFAULT_MODEL_KEY)
    assert default is not None
    assert default.backends["mlx"].status == "stable"
    assert mc.default_model_id("mlx") == default.backends["mlx"].model_id


def test_find_by_model_id_resolves_backend() -> None:
    hit = mc.find_by_model_id("mlx-community/gemma-4-E4B-it-4bit")
    assert hit is not None
    model, backend = hit
    assert model.key == "gemma-4-e4b-it"
    assert backend == "mlx"

    cuda_hit = mc.find_by_model_id("google/gemma-4-E4B-it")
    assert cuda_hit is not None
    assert cuda_hit[1] == "cuda"

    assert mc.find_by_model_id("nobody/nothing") is None


def test_allowed_model_ids_spans_backends() -> None:
    ids = mc.allowed_model_ids()
    assert "mlx-community/Qwen2.5-3B-Instruct-4bit" in ids
    assert "Qwen/Qwen2.5-3B-Instruct" in ids


# ---------------------------------------------------------------------------
# A3 — validation rules
# ---------------------------------------------------------------------------

def test_valid_model_and_backend_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    err = mc.validate_run_request("mlx-community/gemma-3-4b-it-4bit", "mlx")
    assert err is None


def test_unknown_model_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    err = mc.validate_run_request("totally/made-up", "mlx")
    assert err is not None
    assert "catalog" in err.lower()


def test_broken_model_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    err = mc.validate_run_request("mlx-community/gemma-3n-E2B-it-bf16", "mlx")
    assert err is not None
    assert "broken" in err.lower()


def test_backend_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    # An MLX checkpoint requested on the (future) cuda backend.
    err = mc.validate_run_request("mlx-community/gemma-3-4b-it-4bit", "cuda")
    assert err is not None


def test_cuda_id_passes_for_cuda_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    assert mc.validate_run_request("google/gemma-3-4b-it", "cuda") is None


@pytest.mark.parametrize("off", ["false", "0", "no", "off", "FALSE"])
def test_enforcement_escape_hatch(
    monkeypatch: pytest.MonkeyPatch, off: str
) -> None:
    monkeypatch.setenv("SLM_FORGE_ENFORCE_CATALOG", off)
    assert mc.validate_run_request("totally/made-up", "mlx") is None
    assert mc.validate_run_request("mlx-community/gemma-3n-E2B-it-bf16", "mlx") is None


# ---------------------------------------------------------------------------
# Dynamic registry overlay — user-registered models merged with the seeds
# ---------------------------------------------------------------------------

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from apps.api.models.registered_model import RegisteredModel  # noqa: E402
from apps.api.services import db as db_module  # noqa: E402


@pytest.fixture()
def db_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    eng = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.delenv("SLM_FORGE_ENFORCE_CATALOG", raising=False)
    yield
    eng.dispose()


def _register(**over: object) -> RegisteredModel:
    row = RegisteredModel(
        key="qwen2.5-1.5b-instruct",
        label="Qwen 2.5 1.5B Instruct",
        family="qwen",
        size_params="1.5B",
        backend="cuda",
        model_id="Qwen/Qwen2.5-1.5B-Instruct",
        min_memory_gb=6,
        quant="nf4",
        status="untested",
        gated=False,
        notes="registered via test",
        created_by_user_id="alice",
        created_by_tenant_id="acme",
    )
    for k, v in over.items():
        setattr(row, k, v)
    with Session(db_module.engine) as s:
        s.add(row)
        s.commit()
    return row


def test_seeds_present_without_registry(db_engine: None) -> None:
    keys = {m.key for m in mc.effective_catalog()}
    assert {m.key for m in mc.CATALOG_V2} <= keys


def test_registered_model_appears_in_catalog(db_engine: None) -> None:
    _register()
    hit = mc.find_by_model_id("Qwen/Qwen2.5-1.5B-Instruct")
    assert hit is not None
    model, backend = hit
    assert model.key == "qwen2.5-1.5b-instruct"
    assert backend == "cuda"
    assert "Qwen/Qwen2.5-1.5B-Instruct" in mc.allowed_model_ids()
    assert mc.get_model_by_key("qwen2.5-1.5b-instruct") is not None


def test_validate_accepts_registered_model(db_engine: None) -> None:
    _register()
    assert mc.validate_run_request("Qwen/Qwen2.5-1.5B-Instruct", "cuda") is None


def test_validate_rejects_backend_mismatch_registered(db_engine: None) -> None:
    _register()  # cuda variant only
    err = mc.validate_run_request("Qwen/Qwen2.5-1.5B-Instruct", "mlx")
    assert err is not None


def test_validate_rejects_broken_registered(db_engine: None) -> None:
    _register(status="broken", notes="does not converge")
    err = mc.validate_run_request("Qwen/Qwen2.5-1.5B-Instruct", "cuda")
    assert err is not None and "broken" in err.lower()


def test_registered_backend_merges_into_existing_seed_key(db_engine: None) -> None:
    seed = mc.CATALOG_V2[0]
    other_backend = "cuda" if "mlx" in seed.backends else "mlx"
    _register(key=seed.key, backend=other_backend, model_id="registered/extra-variant")
    merged = {m.key: m for m in mc.effective_catalog()}[seed.key]
    assert set(seed.backends) <= set(merged.backends)
    assert other_backend in merged.backends


def test_falls_back_to_seeds_when_registry_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Boom:
        def __getattr__(self, _name: str) -> object:
            raise RuntimeError("registry unavailable")

    monkeypatch.setattr(db_module, "engine", _Boom())
    keys = {m.key for m in mc.effective_catalog()}
    assert {m.key for m in mc.CATALOG_V2} <= keys
