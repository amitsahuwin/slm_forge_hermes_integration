"""Phase D.3 — per-user dataset isolation on /api/v1/datasets.

* ``global/<name>/`` (bundled samples) is visible to every authenticated user.
* User-uploaded datasets at ``users/{tenant}/{user}/<name>/`` are visible
  only to the owner; tenant admins see every user in their tenant.
* Path-traversal in dataset names is rejected.
"""
from __future__ import annotations

import pytest

from apps.api.services import identity_paths
from apps.api.services.identity_paths import safe_name


def _make_dataset(root, name="demo") -> None:
    ds = root / name
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "train.jsonl").write_text('{"text": "t"}\n')
    (ds / "valid.jsonl").write_text('{"text": "v"}\n')


@pytest.fixture()
def datasets_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(identity_paths, "DATASETS_ROOT", tmp_path)
    return tmp_path


def test_safe_name_rejects_traversal_and_separators():
    for bad in ["..", "../etc", "a/b", "/abs", ".hidden", "", "x" * 201]:
        with pytest.raises(ValueError):
            safe_name(bad)
    # Happy path
    for good in ["demo", "alice-set_1.v2", "X9"]:
        assert safe_name(good) == good


def test_global_visible_to_every_user(datasets_root):
    from apps.api.routers.datasets import list_datasets
    from tests.api._isolation_helpers import make_request

    _make_dataset(datasets_root / "global", "shared")

    for tenant, user in [("acme", "alice@acme"), ("globex", "carol@globex"), ("local", "admin@local")]:
        req = make_request(user_id=user, tenant=tenant, role="data_engineer")
        result = list_datasets(req)
        names = {d.name for d in result}
        assert "shared" in names, f"global/shared not visible to {user}"


def test_user_dataset_not_visible_to_others(datasets_root):
    from apps.api.routers.datasets import list_datasets
    from tests.api._isolation_helpers import make_request

    # Alice uploads "alice-only" under her per-user dir.
    alice_dir = datasets_root / "users" / "acme" / "alice@acme"
    _make_dataset(alice_dir, "alice-only")

    # Bob (same tenant, non-admin) should NOT see alice-only.
    bob = make_request(user_id="bob@acme", tenant="acme", role="data_engineer")
    bob_names = {d.name for d in list_datasets(bob)}
    assert "alice-only" not in bob_names

    # Carol in a different tenant should NOT see it either.
    carol = make_request(user_id="carol@globex", tenant="globex", role="admin")
    carol_names = {d.name for d in list_datasets(carol)}
    assert "alice-only" not in carol_names

    # Alice herself sees it.
    alice = make_request(user_id="alice@acme", tenant="acme", role="data_engineer")
    alice_names = {d.name for d in list_datasets(alice)}
    assert "alice-only" in alice_names


def test_tenant_admin_sees_all_users_in_their_tenant(datasets_root):
    from apps.api.routers.datasets import list_datasets
    from tests.api._isolation_helpers import make_request

    _make_dataset(datasets_root / "users" / "acme" / "alice@acme", "alice-set")
    _make_dataset(datasets_root / "users" / "acme" / "bob@acme", "bob-set")
    _make_dataset(datasets_root / "users" / "globex" / "carol@globex", "carol-set")

    admin_acme = make_request(user_id="alice@acme", tenant="acme", role="admin")
    names = {d.name for d in list_datasets(admin_acme)}
    assert "alice-set" in names
    assert "bob-set" in names
    assert "carol-set" not in names, "tenant-admin must not see other tenants"