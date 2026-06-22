"""Fingerprinting + secret redaction for captured exceptions.

The fingerprint is the dedupe key — same exception class + same top-3
project frames must produce the same fingerprint across processes so the
GitHub-issue path can comment on an existing issue instead of opening a
duplicate. Line numbers are deliberately excluded so a one-line code drift
above the raise site doesn't split an issue.

Secrets MUST be redacted before traceback text enters either the SDK
prompt or the GitHub issue body (CLAUDE.md rule 28). The list is
conservative — false positives are acceptable; false negatives are not.
"""
from __future__ import annotations

import hashlib
import os
import re
import traceback
from pathlib import Path

# Patterns are anchored to be greedy on the secret-shaped substring but
# narrow enough to leave the surrounding traceback context readable.
_REDACTORS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer ***"),
    # api_key= / password= / token= / secret= in any case + with quotes
    (
        re.compile(
            r"(?i)(api[_-]?key|password|token|secret)\s*[:=]\s*['\"]?[^'\"\s,;}]+",
        ),
        r"\1=***",
    ),
    # AWS access keys
    (re.compile(r"\bAKIA[0-9A-Z]{12,20}\b"), "AKIA***"),
    # Anthropic / OpenAI-style sk-* keys
    (re.compile(r"\bsk-(ant|live|proj)-[A-Za-z0-9_\-]+"), "sk-***"),
    # JWTs (three base64url segments separated by dots; require leading eyJ)
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "<jwt-redacted>",
    ),
    # Email addresses — show domain only would be nicer but lose nothing here.
    (re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"), "<email-redacted>"),
)


def redact(text: str) -> str:
    """Apply every pattern in order. Idempotent: safe to call twice."""
    if not text:
        return text
    out = text
    for pat, repl in _REDACTORS:
        out = pat.sub(repl, out)
    return out


def extract_top_project_frame(
    tb: traceback.StackSummary, project_root: Path | None = None
) -> tuple[str, str, int] | None:
    """Walk the traceback (innermost first) and return the first frame whose
    file lives under ``project_root``. Returns ``(relpath, funcname, lineno)``.

    Stdlib + site-packages frames are skipped — we want the code WE own.
    If nothing matches (all frames are stdlib), returns ``None``.
    """
    root = (project_root or Path.cwd()).resolve()
    # ``traceback.StackSummary`` is outer-to-inner; the LAST entry is where
    # the exception originated.
    for frame in reversed(tb):
        try:
            p = Path(frame.filename).resolve()
        except (OSError, ValueError):
            continue
        # Heuristic: must live under project_root and NOT under any venv /
        # site-packages / stdlib path.
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        parts = set(rel.parts)
        if parts & {"site-packages", ".venv", "venv", "__pycache__"}:
            continue
        return (str(rel), frame.name, int(frame.lineno or 0))
    return None


def fingerprint(
    exc: BaseException, project_root: Path | None = None
) -> str:
    """Compute a stable sha256 of ``exception_type | top-3 project frames``.

    The hash is line-number-insensitive: only ``file:funcname`` per frame.
    """
    root = (project_root or Path.cwd()).resolve()
    tb = traceback.extract_tb(exc.__traceback__)
    project_frames: list[tuple[str, str]] = []
    for frame in reversed(tb):
        try:
            p = Path(frame.filename).resolve()
            rel = p.relative_to(root)
        except (OSError, ValueError):
            continue
        if set(rel.parts) & {"site-packages", ".venv", "venv", "__pycache__"}:
            continue
        project_frames.append((str(rel), frame.name))
        if len(project_frames) >= 3:
            break

    parts = [type(exc).__name__]
    parts.extend(f"{f}::{n}" for f, n in project_frames)
    key = "|".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fingerprint_short(exc: BaseException, project_root: Path | None = None) -> str:
    """First 12 chars of the full sha256 — for display + GitHub search."""
    return fingerprint(exc, project_root)[:12]


def format_traceback(exc: BaseException, *, redact_secrets: bool = True) -> str:
    """Render the exception as a redacted traceback string.

    Falls back to ``type: msg`` when no traceback is attached (rare —
    happens when the exception was constructed without being raised).
    """
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if not tb_str.strip():
        tb_str = f"{type(exc).__name__}: {exc}"
    return redact(tb_str) if redact_secrets else tb_str


def project_root_from_env(default: Path | None = None) -> Path:
    """Resolve the project root used for relative-frame matching."""
    env_root = os.environ.get("SLM_FORGE_PROJECT_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return (default or Path.cwd()).resolve()
