"""Phase D — PATCH endpoints must NOT honour client-supplied tenant_id
or user_id values. The *Patch Pydantic models simply don't declare those
fields; this test pins that assumption.
"""
from __future__ import annotations

import pytest

from apps.api.routers import exports as exports_router
from apps.api.routers import runs as runs_router
from apps.api.routers import sessions as sessions_router


@pytest.mark.parametrize(
    "patch_cls",
    [
        runs_router.RunPatch,
        sessions_router.SessionPatch,
        exports_router.ExportPatch,
    ],
)
def test_patch_model_has_no_identity_fields(patch_cls):
    fields = set(patch_cls.model_fields.keys())
    assert "tenant_id" not in fields, (
        f"{patch_cls.__name__} must not declare tenant_id — clients cannot "
        "be allowed to mutate the tenant boundary."
    )
    assert "user_id" not in fields, (
        f"{patch_cls.__name__} must not declare user_id — ownership is "
        "immutable after create."
    )
    assert "role" not in fields, (
        f"{patch_cls.__name__} must not declare role — captured at write time."
    )