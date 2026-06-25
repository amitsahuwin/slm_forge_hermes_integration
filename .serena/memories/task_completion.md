# Task Completion — Run these before declaring done

Order matters: cheapest first, then tests, then frontend build (which is the type gate for the UI).

## 1. Python lint (changed files only — repo has pre-existing findings)
```
uv run ruff check --fix <changed-paths>
```

## 2. Python type-check
```
uv run mypy apps packages
```
Strict mode is on; ignore_missing_imports is on. New type errors block.

## 3. Python tests
```
uv run pytest -q
```
Single file/test for fast iteration:
```
uv run pytest tests/api/test_run_validation.py
uv run pytest tests/api/test_run_validation.py::test_broken_model_is_422
```
TDD red→green→refactor: write failing tests first, all green at end. Never delete/weaken tests.

## 4. Frontend type + build gate (only if `apps/web/` touched)
```
cd apps/web && npm run build           # tsc --noEmit && vite build
```
`npm run typecheck` (`tsc --noEmit`) is fine for a faster intermediate check.

## 5. OPA policy tests (only if `policies/` touched)
```
make opa-test
```

## 6. Refresh graph (after code edits)
```
graphify update .
```

## 7. DoD checklist (CLAUDE.md gate — confirm before commit)
- Spec + plan updated; ≥3 clean red-team passes if architecture changed.
- Tests written first, all green, meaningful coverage ≥90%.
- No hardcoded secrets/env values; no `*_v#` modules.
- AAA enforced where relevant; errors handled; logs/metrics/health intact; no secrets/PII logged.
- DRY/YAGNI; lint/type clean; data in DB (not disk); migrations reversible + backed up; tenant isolation intact.
- `README` / `Makefile` / ADRs / `release/` updated.

## 8. Commit
```
# write commit_message.md (gitignored) — Conventional Commits, what + why
git add .
git commit -F commit_message.md
```
Never `--no-verify`. Never force-push shared branches. Never commit secrets.

## 9. Handover
Give the user: short change summary, link to release notes (`release/`), and how to verify it (UI click steps and/or runnable `curl` examples).
