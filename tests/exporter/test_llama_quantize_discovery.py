"""Phase T / T3 — cross-platform llama-quantize discovery.

The exporter must locate ``llama-quantize`` not just in Homebrew prefixes
(macOS) but on ``PATH`` and in a local llama.cpp source build (Linux), so a
CUDA host that built llama.cpp from source can export GGUFs.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

# pipeline imports httpx at module load; skip cleanly where it's unavailable.
pipeline = pytest.importorskip("packages.exporter.pipeline")


def _make_exe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_path_hit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "bin" / "llama-quantize"
    _make_exe(exe)
    monkeypatch.setenv("PATH", str(exe.parent) + os.pathsep + os.environ.get("PATH", ""))
    found = pipeline._find_llama_quantize()
    assert found is not None
    assert Path(found).name == "llama-quantize"


def test_local_source_build_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No PATH hit; the binary lives in scripts/llama_cpp_src/build/bin/.
    monkeypatch.setattr(pipeline.shutil, "which", lambda _name: None)
    local = tmp_path / "build" / "bin" / "llama-quantize"
    _make_exe(local)
    monkeypatch.setattr(pipeline, "LOCAL_QUANTIZE", local)
    found = pipeline._find_llama_quantize()
    assert found == str(local)


def test_absent_everywhere_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline.shutil, "which", lambda _name: None)
    monkeypatch.setattr(pipeline, "LOCAL_QUANTIZE", tmp_path / "nope" / "llama-quantize")
    # Make the hardcoded system prefixes miss too.
    monkeypatch.setattr(pipeline.os, "access", lambda _p, _mode: False)
    assert pipeline._find_llama_quantize() is None
