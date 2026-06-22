"""PR-4 — qa_store behaviour: lifecycle, capacity LRU, TTL, locking, error paths."""
from __future__ import annotations

import time

import pytest

from apps.api.services import qa_store
from apps.api.services.qa_store import QAResult, QAWarning


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch):
    qa_store.clear()
    # Tests configure their own caps/TTLs via env where needed; otherwise
    # leave the defaults.
    monkeypatch.delenv("HERMES_QA_ENABLED", raising=False)
    monkeypatch.delenv("HERMES_QA_TIMEOUT_S", raising=False)
    monkeypatch.delenv("HERMES_QA_CACHE_TTL_S", raising=False)
    monkeypatch.delenv("HERMES_QA_CACHE_CAP", raising=False)
    yield
    qa_store.clear()


def test_new_id_returns_unique_hex():
    a = qa_store.new_id()
    b = qa_store.new_id()
    assert a != b
    assert len(a) == 12 and len(b) == 12


def test_init_pending_then_get_returns_pending():
    qid = qa_store.new_id()
    qa_store.init_pending(qid)
    out = qa_store.get(qid)
    assert out is not None
    assert out.status == "pending"
    assert out.warnings == []


def test_set_result_overwrites_in_place():
    qid = qa_store.new_id()
    qa_store.init_pending(qid)
    qa_store.set_result(
        qid,
        QAResult(
            status="ready",
            overall_health="good",
            summary="all clear",
            warnings=[QAWarning(severity="low", category="other", message="x")],
        ),
    )
    out = qa_store.get(qid)
    assert out is not None
    assert out.status == "ready"
    assert out.warnings[0].message == "x"


def test_capacity_eviction_drops_oldest(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_QA_CACHE_CAP", "3")
    ids = []
    for _ in range(5):
        qid = qa_store.new_id()
        qa_store.init_pending(qid)
        ids.append(qid)
    # Only the 3 most recent should survive.
    assert qa_store.get(ids[0]) is None
    assert qa_store.get(ids[1]) is None
    for qid in ids[2:]:
        assert qa_store.get(qid) is not None


def test_ttl_eviction_purges_old_entries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_QA_CACHE_TTL_S", "0.01")  # 10ms TTL
    qid = qa_store.new_id()
    qa_store.init_pending(qid)
    time.sleep(0.05)  # exceed the TTL
    assert qa_store.get(qid) is None


def test_lock_for_returns_same_lock_for_same_key():
    a = qa_store.lock_for("k1")
    b = qa_store.lock_for("k1")
    assert a is b
    c = qa_store.lock_for("k2")
    assert c is not a


@pytest.mark.asyncio
async def test_run_qa_persists_ready_result(monkeypatch: pytest.MonkeyPatch):
    """Skill returns valid JSON → result stored as ready with parsed warnings."""
    qid = qa_store.new_id()
    qa_store.init_pending(qid)

    fake_response = '''
    {
      "overall_health": "fair",
      "summary": "Lots of duplicates in the seed set.",
      "issues": [
        {"severity": "high", "kind": "duplicates",
         "description": "12 duplicate prompts detected",
         "affected_count": 12, "fix": "Deduplicate before training"}
      ],
      "ready_to_train": false
    }
    '''

    from apps.api.services import dataset_qa as dq

    monkeypatch.setattr(dq, "_invoke_skill", lambda rows: fake_response)

    await qa_store.run_qa(qid, [{"prompt": "x"} for _ in range(3)])

    out = qa_store.get(qid)
    assert out is not None
    assert out.status == "ready"
    assert out.overall_health == "fair"
    assert out.ready_to_train is False
    assert len(out.warnings) == 1
    assert out.warnings[0].severity == "high"
    assert out.warnings[0].category == "duplicates"
    assert out.warnings[0].affected_count == 12


@pytest.mark.asyncio
async def test_run_qa_handles_hermes_failure(monkeypatch: pytest.MonkeyPatch):
    """Skill raises → status flips to unavailable; never bubbles."""
    import httpx

    qid = qa_store.new_id()
    qa_store.init_pending(qid)

    from apps.api.services import dataset_qa as dq

    def explode(rows):
        raise httpx.ConnectError("ollama down")

    monkeypatch.setattr(dq, "_invoke_skill", explode)

    await qa_store.run_qa(qid, [{"prompt": "x"}])

    out = qa_store.get(qid)
    assert out is not None
    assert out.status == "unavailable"
    assert out.error is not None
    assert "ConnectError" in out.error


@pytest.mark.asyncio
async def test_run_qa_handles_invalid_json(monkeypatch: pytest.MonkeyPatch):
    qid = qa_store.new_id()
    qa_store.init_pending(qid)

    from apps.api.services import dataset_qa as dq

    monkeypatch.setattr(dq, "_invoke_skill", lambda rows: "not valid json at all")

    await qa_store.run_qa(qid, [{"prompt": "x"}])

    out = qa_store.get(qid)
    assert out is not None
    # _parse degrades malformed JSON to unavailable rather than raising.
    assert out.status == "unavailable"


@pytest.mark.asyncio
async def test_run_qa_timeout_marks_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_QA_TIMEOUT_S", "0.05")

    qid = qa_store.new_id()
    qa_store.init_pending(qid)

    from apps.api.services import dataset_qa as dq

    def slow(rows):
        time.sleep(0.5)
        return '{"issues": []}'

    monkeypatch.setattr(dq, "_invoke_skill", slow)

    await qa_store.run_qa(qid, [{"prompt": "x"}])

    out = qa_store.get(qid)
    assert out is not None
    assert out.status == "unavailable"
    assert out.error is not None
    assert "timed out" in out.error.lower()


@pytest.mark.asyncio
async def test_run_qa_short_circuits_when_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HERMES_QA_ENABLED", "false")
    called = {"n": 0}

    from apps.api.services import dataset_qa as dq

    monkeypatch.setattr(dq, "_invoke_skill", lambda rows: (called.__setitem__("n", 1), "{}")[1])

    qid = qa_store.new_id()
    qa_store.init_pending(qid)

    await qa_store.run_qa(qid, [{"prompt": "x"}])

    assert called["n"] == 0
    # Slot remains in ``pending`` — disabled feature should not flip status.
    out = qa_store.get(qid)
    assert out is not None and out.status == "pending"


@pytest.mark.asyncio
async def test_run_qa_dedupes_when_already_ready(monkeypatch: pytest.MonkeyPatch):
    """A second run_qa for an already-ready id is a no-op (first result wins)."""
    qid = qa_store.new_id()
    qa_store.set_result(qid, QAResult(status="ready", summary="already done"))

    from apps.api.services import dataset_qa as dq

    called = {"n": 0}
    monkeypatch.setattr(
        dq,
        "_invoke_skill",
        lambda rows: (called.__setitem__("n", called["n"] + 1), '{"issues":[]}')[1],
    )

    await qa_store.run_qa(qid, [{"prompt": "x"}])

    assert called["n"] == 0
    out = qa_store.get(qid)
    assert out is not None
    assert out.summary == "already done"
