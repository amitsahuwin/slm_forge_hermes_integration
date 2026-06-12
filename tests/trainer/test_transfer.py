"""Phase R / A8 — worker-side dataset download + adapter upload helpers."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.trainer import transfer


def _dataset_tar(name: str = "demo") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for fname, data in [
            (f"{name}/train.jsonl", b'{"text": "a"}\n'),
            (f"{name}/valid.jsonl", b'{"text": "b"}\n'),
        ]:
            info = tarfile.TarInfo(name=fname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_ensure_dataset_local_short_circuits_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = tmp_path / "demo"
    ds.mkdir()
    (ds / "train.jsonl").write_text("{}\n")
    monkeypatch.setattr(transfer, "DATA_ROOT", tmp_path)

    def boom(*a, **kw):  # any HTTP call is a failure
        raise AssertionError("should not hit the network")

    monkeypatch.setattr(transfer.httpx, "get", boom)
    assert transfer.ensure_dataset_local("demo", "http://api") == ds


def test_ensure_dataset_local_downloads_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer, "DATA_ROOT", tmp_path)
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return SimpleNamespace(
            status_code=200,
            content=_dataset_tar("demo"),
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(transfer.httpx, "get", fake_get)
    ds = transfer.ensure_dataset_local("demo", "http://api")

    assert ds == tmp_path / "demo"
    assert (ds / "train.jsonl").read_text() == '{"text": "a"}\n'
    assert calls == ["http://api/api/v1/datasets/demo/archive"]


def test_ensure_dataset_local_rejects_traversal_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer, "DATA_ROOT", tmp_path)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name="../evil.txt")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))

    monkeypatch.setattr(
        transfer.httpx, "get",
        lambda url, **kw: SimpleNamespace(
            status_code=200, content=buf.getvalue(), raise_for_status=lambda: None
        ),
    )
    with pytest.raises(transfer.TransferError):
        transfer.ensure_dataset_local("demo", "http://api")
    assert not (tmp_path.parent / "evil.txt").exists()


def test_upload_adapter_posts_multipart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_model.safetensors").write_bytes(b"\0\1")

    posted: dict = {}

    def fake_post(url, files=None, **kw):
        posted["url"] = url
        posted["bytes"] = files["archive"][1]
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    monkeypatch.setattr(transfer.httpx, "post", fake_post)
    ok = transfer.upload_adapter(7, adapter, "http://api")

    assert ok is True
    assert posted["url"] == "http://api/api/v1/runs/7/artifacts"
    with tarfile.open(fileobj=io.BytesIO(posted["bytes"]), mode="r:gz") as tf:
        assert "adapter/adapter_model.safetensors" in tf.getnames()


def test_upload_adapter_survives_api_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "x.bin").write_bytes(b"\0")

    def fake_post(url, **kw):
        raise transfer.httpx.ConnectError("api down")

    monkeypatch.setattr(transfer.httpx, "post", fake_post)
    assert transfer.upload_adapter(7, adapter, "http://api") is False
