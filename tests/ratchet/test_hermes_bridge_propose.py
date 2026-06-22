"""PR-1 A2 — `propose_mutation` raises `MutationProposalError` on bad output.

Before: invalid JSON or validation errors silently returned a fabricated
``MutationProposal`` whose ``reasoning`` claimed "LR halving" but actually
changed no fields. This test pins the new contract:

  - JSON decode error → ``MutationProposalError`` (chained from the original).
  - Pydantic validation error → ``MutationProposalError``.
  - Happy path → returns a real ``MutationProposal``.

A separate ``test_loop_proposal_failure.py`` checks the ratchet loop's
handling of the new exception type.
"""
from __future__ import annotations

import json

import pytest

import packages.ratchet.hermes_bridge as hb
from packages.ratchet.hermes_bridge import MutationProposal, MutationProposalError


@pytest.fixture(autouse=True)
def _no_trace(monkeypatch):
    monkeypatch.setattr(hb, "_record_trace", lambda **_: None)


def test_propose_raises_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hb, "_call_ollama", lambda *a, **kw: "definitely not json")
    with pytest.raises(MutationProposalError) as ei:
        hb.propose_mutation(dataset="d", history=[], current_best_metric=None)
    assert "JSONDecodeError" in str(ei.value)
    # Original exception is chained (rule 16 + CLAUDE.md: contain + surface).
    assert ei.value.__cause__ is not None
    assert isinstance(ei.value.__cause__, json.JSONDecodeError)


def test_propose_raises_on_validation_error(monkeypatch: pytest.MonkeyPatch):
    # learning_rate=-1 violates the ge=1e-7 bound on MutationProposal.
    monkeypatch.setattr(hb, "_call_ollama", lambda *a, **kw: json.dumps({"learning_rate": -1.0}))
    with pytest.raises(MutationProposalError) as ei:
        hb.propose_mutation(dataset="d", history=[], current_best_metric=None)
    assert "ValidationError" in str(ei.value)


def test_propose_returns_valid_proposal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        hb,
        "_call_ollama",
        lambda *a, **kw: json.dumps(
            {
                "learning_rate": 1e-4,
                "batch_size": 4,
                "reasoning": "halving learning rate",
                "expected_outcome": "more stable training",
            }
        ),
    )
    p = hb.propose_mutation(dataset="d", history=[], current_best_metric=None)
    assert isinstance(p, MutationProposal)
    assert p.learning_rate == 1e-4
    assert p.batch_size == 4


def test_propose_does_not_fabricate_lr_halving_fallback(monkeypatch: pytest.MonkeyPatch):
    """The legacy 'silent LR halving' fallback must be gone — assert it doesn't slip back in."""
    monkeypatch.setattr(hb, "_call_ollama", lambda *a, **kw: "")
    with pytest.raises(MutationProposalError):
        hb.propose_mutation(dataset="d", history=[], current_best_metric=None)
