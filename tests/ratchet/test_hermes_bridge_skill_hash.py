"""Skill-content hashing on every Hermes call.

Hermes itself does not write skill files at runtime — they live in
``.hermes-skills/`` and only change via git commit or manual edit. But
the user needs the Traces tab to *notice* when a skill's content
changes between calls (committed update, hot-reload, or hypothetical
future Hermes auto-write). The cheapest honest signal is to hash the
markdown at load time and persist the hash on the trace row.

These tests pin:
  * ``load_skill`` returns ``(text, sha256_hex, mtime)`` instead of just text.
  * ``_record_trace`` accepts + persists ``skill_name``, ``skill_sha256``,
    ``skill_mtime``.
  * Editing the skill file between two calls yields two trace rows with
    different ``skill_sha256`` — that's the detection signal the UI surfaces.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.models.hermes_trace import HermesTrace
from apps.api.services import db as db_module


@pytest.fixture()
def isolated_engine(tmp_path, monkeypatch: pytest.MonkeyPatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'trace.db'}")
    SQLModel.metadata.create_all(eng, tables=[HermesTrace.__table__])  # type: ignore[arg-type]
    monkeypatch.setattr(db_module, "engine", eng)
    return eng


@pytest.fixture()
def skill_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point HERMES_SKILLS_DIR at a temp dir we control."""
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setenv("HERMES_SKILLS_DIR", str(d))
    # ``hermes_bridge`` reads SKILLS_DIR at import time; rebind the module-level.
    import packages.ratchet.hermes_bridge as hb

    monkeypatch.setattr(hb, "SKILLS_DIR", d)
    return d


# ---------------------------------------------------------------------------
# load_skill returns (text, sha256, mtime)
# ---------------------------------------------------------------------------


def test_load_skill_returns_text_hash_and_mtime(skill_dir: Path) -> None:
    """The new contract: ``load_skill`` returns a 3-tuple. Callers that
    only need the text destructure ``text, _, _ = load_skill(...)``."""
    import packages.ratchet.hermes_bridge as hb

    body = "# Test skill\n\nDo a thing.\n"
    (skill_dir / "demo.md").write_text(body, encoding="utf-8")

    result = hb.load_skill("demo")
    assert result is not None, "load_skill should return a tuple, not None, for a present skill"
    text, sha256_hex, mtime = result
    assert text == body
    assert sha256_hex == hashlib.sha256(body.encode("utf-8")).hexdigest()[:16], (
        "sha256 must be the first 16 hex chars of sha256(skill_text) "
        "to match _log_response_meta's convention"
    )
    assert isinstance(mtime, datetime)
    assert mtime.tzinfo is not None, "mtime must be timezone-aware (UTC)"


def test_load_skill_missing_returns_none(skill_dir: Path) -> None:
    """No fabricated fallback (CLAUDE.md rule 16) — missing skill must be
    surfaced honestly so callers can decide what to do."""
    import packages.ratchet.hermes_bridge as hb

    assert hb.load_skill("does-not-exist") is None


def test_load_skill_falls_back_to_repo_skills_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``~/.hermes/skills/`` is empty, the repo's ``.hermes-skills/``
    is the source of truth. The fallback still returns the 3-tuple."""
    empty = tmp_path / "skills"
    empty.mkdir()
    monkeypatch.setenv("HERMES_SKILLS_DIR", str(empty))
    import packages.ratchet.hermes_bridge as hb

    monkeypatch.setattr(hb, "SKILLS_DIR", empty)
    # ``error_remedy`` is committed to ``.hermes-skills/`` (see PR-3).
    result = hb.load_skill("error_remedy")
    assert result is not None
    text, sha256_hex, mtime = result
    assert len(sha256_hex) == 16
    assert text.strip(), "real skill file should have content"
    assert isinstance(mtime, datetime)


# ---------------------------------------------------------------------------
# _record_trace persists the new fields
# ---------------------------------------------------------------------------


def test_record_trace_persists_skill_fields(isolated_engine, skill_dir: Path) -> None:
    """When the caller passes skill_name / skill_sha256 / skill_mtime,
    the row should reflect them on read-back."""
    import packages.ratchet.hermes_bridge as hb

    body = "skill body v1\n"
    (skill_dir / "demo.md").write_text(body, encoding="utf-8")
    _text, sha256_hex, mtime = hb.load_skill("demo")  # type: ignore[misc]

    hb._record_trace(
        source="skill:demo",
        request_body={"prompt": "x"},
        response_text='{"ok": 1}',
        error=None,
        duration_ms=10,
        attempts=1,
        skill_name="demo",
        skill_sha256=sha256_hex,
        skill_mtime=mtime,
    )

    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.skill_name == "demo"
        assert row.skill_sha256 == sha256_hex
        assert row.skill_mtime is not None
        assert row.success is True


def test_record_trace_marks_success_false_on_error(isolated_engine) -> None:
    """``success`` is materialised from ``error is None`` so the UI can
    filter on success/error without scanning the error column."""
    import packages.ratchet.hermes_bridge as hb

    hb._record_trace(
        source="skill:demo",
        request_body={},
        response_text="",
        error="boom",
        duration_ms=99,
        attempts=2,
    )
    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.success is False
        assert row.error == "boom"


def test_record_trace_skill_fields_optional(isolated_engine) -> None:
    """For non-skill traces (e.g. ``source='chat'``) the skill fields stay
    NULL — they're not synthesised."""
    import packages.ratchet.hermes_bridge as hb

    hb._record_trace(
        source="chat",
        request_body={},
        response_text="",
        error=None,
        duration_ms=1,
        attempts=1,
    )
    with Session(isolated_engine) as s:
        row = s.exec(select(HermesTrace)).one()
        assert row.skill_name is None
        assert row.skill_sha256 is None
        assert row.skill_mtime is None


# ---------------------------------------------------------------------------
# Hash changes between calls when the file changes
# ---------------------------------------------------------------------------


def test_hash_changes_between_calls_when_skill_edited(
    isolated_engine, skill_dir: Path
) -> None:
    """The detection signal: edit the skill file, the next call writes a
    different ``skill_sha256``. Surfacing this in the UI as 'skill content
    changed since last call' is the user-visible feature."""
    import packages.ratchet.hermes_bridge as hb

    (skill_dir / "demo.md").write_text("v1 body\n", encoding="utf-8")
    _text1, sha1, mtime1 = hb.load_skill("demo")  # type: ignore[misc]
    hb._record_trace(
        source="skill:demo",
        request_body={},
        response_text="",
        error=None,
        duration_ms=1,
        attempts=1,
        skill_name="demo",
        skill_sha256=sha1,
        skill_mtime=mtime1,
    )

    (skill_dir / "demo.md").write_text("v2 different body\n", encoding="utf-8")
    _text2, sha2, mtime2 = hb.load_skill("demo")  # type: ignore[misc]
    assert sha1 != sha2, "edit must change the hash"
    hb._record_trace(
        source="skill:demo",
        request_body={},
        response_text="",
        error=None,
        duration_ms=1,
        attempts=1,
        skill_name="demo",
        skill_sha256=sha2,
        skill_mtime=mtime2,
    )

    with Session(isolated_engine) as s:
        rows = s.exec(
            select(HermesTrace).order_by(HermesTrace.id)  # type: ignore[arg-type]
        ).all()
        assert [r.skill_sha256 for r in rows] == [sha1, sha2]


def test_unchanged_file_keeps_same_hash(isolated_engine, skill_dir: Path) -> None:
    """No hash churn when the file isn't touched: two successive calls
    produce identical ``skill_sha256``."""
    import packages.ratchet.hermes_bridge as hb

    (skill_dir / "demo.md").write_text("stable body\n", encoding="utf-8")
    _, sha1, _ = hb.load_skill("demo")  # type: ignore[misc]
    _, sha2, _ = hb.load_skill("demo")  # type: ignore[misc]
    assert sha1 == sha2
