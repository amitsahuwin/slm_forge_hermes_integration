"""PR-A — fingerprint stability + the six redaction patterns.

Fingerprint is the dedupe key: same exception class + same project frames
must hash identically. Line numbers are deliberately ignored so a one-line
drift above the raise site doesn't split GitHub issues.
"""
from __future__ import annotations

import re
from pathlib import Path

from packages.error_responder.fingerprint import (
    extract_top_project_frame,
    fingerprint,
    fingerprint_short,
    format_traceback,
    redact,
)

# ── Redaction ─────────────────────────────────────────────────────────


def test_redact_bearer_token():
    assert "Bearer eyJabc" not in redact("Authorization: Bearer eyJabc.def.ghi-rest")
    # Note: ``eyJ…`` matches the JWT pattern too — order matters but EITHER
    # redaction satisfies the contract.
    out = redact("Bearer ghp_secrettoken12345")
    assert "ghp_secrettoken12345" not in out
    assert "Bearer ***" in out


def test_redact_api_key_equals():
    out = redact('api_key="sk-live-abcdef"')
    assert "sk-live-abcdef" not in out
    out2 = redact("password=hunter2 token: ghp_xxx")
    assert "hunter2" not in out2
    assert "ghp_xxx" not in out2


def test_redact_aws_access_key():
    out = redact("creds: AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "AKIA***" in out


def test_redact_anthropic_style_key():
    # Stand-alone secret (no ``api_key=`` wrapper) hits the sk-* pattern directly.
    out = redact("token is sk-ant-api03-XXXXXXXX")
    assert "sk-ant-api03-XXXXXXXX" not in out
    assert "sk-***" in out

    # In the env-var form, the broader api_key= pattern fires first and
    # redacts the whole assignment. Either way the secret never leaks.
    wrapped = redact("ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXX")
    assert "sk-ant-api03-XXXXXXXX" not in wrapped


def test_redact_jwt():
    raw = "token=eyJhbGciOi.eyJzdWIiOi.signaturepart"
    out = redact(raw)
    assert "eyJhbGciOi.eyJzdWIiOi.signaturepart" not in out
    assert "<jwt-redacted>" in out or "***" in out


def test_redact_email():
    out = redact("contact alice@example.com for details")
    assert "alice@example.com" not in out
    assert "<email-redacted>" in out


def test_redact_idempotent():
    raw = "Bearer abc.def and email bob@x.com"
    once = redact(raw)
    twice = redact(once)
    assert once == twice


def test_redact_empty_string():
    assert redact("") == ""


def test_redact_preserves_non_secret_content():
    raw = "Error: file not found at /etc/hosts (errno 2)"
    assert redact(raw) == raw


# ── Fingerprint ───────────────────────────────────────────────────────


def _raise_at_line_a():
    raise ValueError("boom")


def _raise_at_line_b():
    # Same function semantics — exists to confirm function NAME is what matters.
    raise ValueError("boom")


def _grab(fn) -> BaseException:
    try:
        fn()
    except BaseException as e:
        return e
    raise AssertionError("expected exception")


def test_fingerprint_stable_across_invocations():
    e1 = _grab(_raise_at_line_a)
    e2 = _grab(_raise_at_line_a)
    assert fingerprint(e1) == fingerprint(e2)


def test_fingerprint_changes_with_exception_class():
    def _raise_type():
        raise TypeError("nope")

    e_value = _grab(_raise_at_line_a)
    e_type = _grab(_raise_type)
    assert fingerprint(e_value) != fingerprint(e_type)


def test_fingerprint_differs_for_different_function_names():
    e1 = _grab(_raise_at_line_a)
    e2 = _grab(_raise_at_line_b)
    assert fingerprint(e1) != fingerprint(e2), (
        "different function names should produce different fingerprints"
    )


def test_fingerprint_short_is_12_hex():
    e = _grab(_raise_at_line_a)
    short = fingerprint_short(e)
    assert len(short) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", short)


def test_extract_top_project_frame_returns_project_frame():
    e = _grab(_raise_at_line_a)
    tb = __import__("traceback").extract_tb(e.__traceback__)
    out = extract_top_project_frame(tb)
    assert out is not None
    rel, fn, lineno = out
    assert "tests/error_responder/test_fingerprint.py" in rel
    assert fn == "_raise_at_line_a"
    assert lineno > 0


def test_extract_top_project_frame_skips_outside_project(tmp_path: Path):
    """When project_root points elsewhere, no frames match → None."""
    e = _grab(_raise_at_line_a)
    tb = __import__("traceback").extract_tb(e.__traceback__)
    out = extract_top_project_frame(tb, project_root=tmp_path)
    assert out is None


def test_format_traceback_is_redacted():
    def boom():
        secret = "sk-ant-very-secret"
        raise RuntimeError(f"failure with {secret}")

    e = _grab(boom)
    rendered = format_traceback(e)
    assert "sk-ant-very-secret" not in rendered
    assert "RuntimeError" in rendered


def test_format_traceback_no_traceback_fallback():
    """An exception constructed without being raised still produces output."""
    e = ValueError("x")
    out = format_traceback(e)
    assert "ValueError" in out
