"""Phase P / A2 — /models keeps the legacy shape; /models/v2 is the full view."""
from __future__ import annotations

from apps.api.routers.models import BaseModelInfo, list_models, list_models_v2
from apps.api.services import model_catalog as mc

LEGACY_FIELDS = {"hf_id", "label", "family", "size_params", "recommended_method", "notes"}


def test_legacy_endpoint_shape_is_frozen() -> None:
    """The React NewRun/NewExperiment pages read hf_id/label/notes — freeze it."""
    models = list_models()
    assert models, "legacy view must not be empty"
    for m in models:
        assert isinstance(m, BaseModelInfo)
        assert set(m.model_dump().keys()) == LEGACY_FIELDS
        assert m.hf_id.strip() and m.label.strip()


def test_legacy_view_covers_all_mlx_variants() -> None:
    hf_ids = {m.hf_id for m in list_models()}
    expected = {m.backends["mlx"].model_id for m in mc.CATALOG_V2}
    assert hf_ids == expected


def test_legacy_view_default_model_first() -> None:
    assert list_models()[0].hf_id == mc.default_model_id("mlx")


def test_legacy_view_marks_broken_models() -> None:
    gemma3n = next(m for m in list_models() if "gemma-3n" in m.hf_id)
    assert "broken" in gemma3n.label.lower()


def test_v2_endpoint_returns_full_entries() -> None:
    v2 = list_models_v2()
    assert {m.key for m in v2} == {m.key for m in mc.CATALOG_V2}
    sample = next(m for m in v2 if m.key == "gemma-4-e4b-it")
    assert "mlx" in sample.backends and "cuda" in sample.backends
    assert sample.backends["mlx"].min_memory_gb > 0
