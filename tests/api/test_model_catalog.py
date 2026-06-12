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
