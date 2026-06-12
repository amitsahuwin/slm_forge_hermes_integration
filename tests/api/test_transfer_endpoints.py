"""Phase R / A6-A7 — dataset archive download + adapter artifact upload."""
from __future__ import annotations

import io
import tarfile

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from apps.api.models.run import Run


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# A6 — GET /datasets/{name}/archive
# ---------------------------------------------------------------------------

def _make_dataset(root, name="demo"):
    ds = root / name
    ds.mkdir(parents=True)
    (ds / "train.jsonl").write_text('{"text": "a"}\n')
    (ds / "valid.jsonl").write_text('{"text": "b"}\n')
    return ds


def test_archive_roundtrip(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.routers.datasets as ds_router

    monkeypatch.setattr(ds_router, "DATA_ROOT", tmp_path)
    _make_dataset(tmp_path)

    resp = ds_router.download_dataset_archive("demo")
    assert resp.media_type == "application/gzip"

    with tarfile.open(fileobj=io.BytesIO(resp.body), mode="r:gz") as tf:
        names = sorted(tf.getnames())
        assert "demo/train.jsonl" in names
        assert "demo/valid.jsonl" in names
        train = tf.extractfile("demo/train.jsonl").read().decode()
    assert train == '{"text": "a"}\n'


def test_archive_unknown_dataset_404(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.routers.datasets as ds_router

    monkeypatch.setattr(ds_router, "DATA_ROOT", tmp_path)
    with pytest.raises(HTTPException) as exc:
        ds_router.download_dataset_archive("nope")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("bad", ["../etc", "a/b", "demo/..", ".hidden", ""])
def test_archive_rejects_bad_names(
    tmp_path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    import apps.api.routers.datasets as ds_router

    monkeypatch.setattr(ds_router, "DATA_ROOT", tmp_path)
    with pytest.raises(HTTPException) as exc:
        ds_router.download_dataset_archive(bad)
    assert exc.value.status_code in (404, 422)


# ---------------------------------------------------------------------------
# A7 — POST /runs/{id}/artifacts
# ---------------------------------------------------------------------------

def _adapter_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _upload(db, run_id: int, payload: bytes):
    from starlette.datastructures import UploadFile

    import apps.api.routers.runs as runs_router

    return runs_router.upload_run_artifacts(
        run_id, UploadFile(io.BytesIO(payload), filename="adapter.tar.gz"), db
    )


def test_upload_extracts_adapter(db, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.routers.runs as runs_router

    monkeypatch.setattr(runs_router, "ARTIFACTS_ROOT", tmp_path)
    run = Run(dataset="demo", base_model="m/x")
    db.add(run)
    db.commit()
    db.refresh(run)

    payload = _adapter_tar({
        "adapter/adapter_model.safetensors": b"\0\1",
        "adapter/adapter_config.json": b"{}",
    })
    result = _upload(db, run.id, payload)

    assert result["files"] == 2
    out = tmp_path / str(run.id) / "adapter"
    assert (out / "adapter_model.safetensors").read_bytes() == b"\0\1"
    assert result["adapter_path"].endswith(f"{run.id}/adapter")


def test_upload_unknown_run_404(db, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.routers.runs as runs_router

    monkeypatch.setattr(runs_router, "ARTIFACTS_ROOT", tmp_path)
    with pytest.raises(HTTPException) as exc:
        _upload(db, 999, _adapter_tar({"adapter/x": b"d"}))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("evil", ["../../escape.txt", "/abs/path.txt", "adapter/../../up.txt"])
def test_upload_rejects_traversal(
    db, tmp_path, monkeypatch: pytest.MonkeyPatch, evil: str
) -> None:
    import apps.api.routers.runs as runs_router

    monkeypatch.setattr(runs_router, "ARTIFACTS_ROOT", tmp_path)
    run = Run(dataset="demo", base_model="m/x")
    db.add(run)
    db.commit()
    db.refresh(run)

    payload = _adapter_tar({"adapter/ok.bin": b"d", evil: b"evil"})
    with pytest.raises(HTTPException) as exc:
        _upload(db, run.id, payload)
    assert exc.value.status_code == 400
    # Wholesale rejection: nothing extracted, not even the valid member.
    assert not (tmp_path / str(run.id)).exists() or not any(
        (tmp_path / str(run.id)).rglob("*")
    )


def test_upload_rejects_garbage_archive(
    db, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apps.api.routers.runs as runs_router

    monkeypatch.setattr(runs_router, "ARTIFACTS_ROOT", tmp_path)
    run = Run(dataset="demo", base_model="m/x")
    db.add(run)
    db.commit()
    db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        _upload(db, run.id, b"not a tarball")
    assert exc.value.status_code == 400
