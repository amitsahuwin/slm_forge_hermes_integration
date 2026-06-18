"""Phase U — gated-repo flagging on the backend-aware catalog."""
from __future__ import annotations

from apps.api.services import model_catalog as mc


def test_backend_variant_defaults_not_gated() -> None:
    v = mc.BackendVariant(model_id="x/y", min_memory_gb=1)
    assert v.gated is False


def test_gemma_and_llama_cuda_variants_are_gated() -> None:
    for model_id in (
        "google/gemma-3-4b-it",
        "google/gemma-3-12b-it",
        "meta-llama/Llama-3.2-3B-Instruct",
    ):
        hit = mc.find_by_model_id(model_id)
        assert hit is not None, model_id
        model, backend = hit
        assert model.backends[backend].gated is True, model_id


def test_non_gated_models_are_not_flagged() -> None:
    for model_id in (
        "Qwen/Qwen2.5-3B-Instruct",
        "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "mistralai/Mistral-7B-Instruct-v0.3",
    ):
        hit = mc.find_by_model_id(model_id)
        assert hit is not None, model_id
        model, backend = hit
        assert model.backends[backend].gated is False, model_id


def test_gated_variants_carry_a_note() -> None:
    hit = mc.find_by_model_id("google/gemma-3-4b-it")
    assert hit is not None
    model, backend = hit
    assert "HF_TOKEN" in model.backends[backend].notes
