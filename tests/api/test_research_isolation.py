"""Phase D.3 — per-user research report isolation.

Verifies the in-memory ``_Job`` store + the on-disk ``users/{tenant}/{user}/``
layout both gate report visibility correctly.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.routers import research as research_router
from apps.api.services import identity_paths


def _write_report(root, *, tenant, user, filename, body="# title\n\ncontent\n"):
    d = root / "users" / tenant / user
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body)
    return d / filename


@pytest.fixture()
def reports_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(identity_paths, "REPORTS_ROOT", tmp_path)
    return tmp_path


def test_user_sees_only_own_reports(reports_root):
    from tests.api._isolation_helpers import make_request

    _write_report(reports_root, tenant="acme", user="alice@acme", filename="alice.md")
    _write_report(reports_root, tenant="acme", user="bob@acme", filename="bob.md")
    _write_report(reports_root, tenant="globex", user="carol@globex", filename="carol.md")

    alice = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    rows = research_router.list_reports(alice)
    names = {r.filename for r in rows}
    assert "alice.md" in names
    assert "bob.md" not in names
    assert "carol.md" not in names


def test_tenant_admin_sees_tenant_wide(reports_root):
    from tests.api._isolation_helpers import make_request

    _write_report(reports_root, tenant="acme", user="alice@acme", filename="alice.md")
    _write_report(reports_root, tenant="acme", user="bob@acme", filename="bob.md")
    _write_report(reports_root, tenant="globex", user="carol@globex", filename="carol.md")

    admin = make_request(user_id="alice@acme", tenant="acme", role="admin")
    rows = research_router.list_reports(admin)
    names = {r.filename for r in rows}
    assert "alice.md" in names
    assert "bob.md" in names
    assert "carol.md" not in names, "tenant-admin must not see other tenants"


def test_get_report_rejects_cross_user(reports_root):
    from tests.api._isolation_helpers import make_request

    _write_report(reports_root, tenant="acme", user="alice@acme", filename="alice.md")

    bob = make_request(user_id="bob@acme", tenant="acme", role="data_engineer")
    with pytest.raises(HTTPException) as exc:
        research_router.get_report("alice.md", bob)
    assert exc.value.status_code == 404


def test_job_visibility_gates_by_tenant_user():
    """The in-memory job store must refuse jobs that don't belong to the
    caller (other-user / other-tenant)."""
    from tests.api._isolation_helpers import make_request

    fake_job = type(
        "J", (), {"tenant_id": "acme", "user_id": "alice@acme"}
    )()
    bob = make_request(user_id="bob@acme", tenant="acme", role="data_engineer")
    alice = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")

    from apps.api.services.identity import current_identity

    assert research_router._job_visible(fake_job, current_identity(alice))
    assert not research_router._job_visible(fake_job, current_identity(bob))