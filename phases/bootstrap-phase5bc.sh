#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SLM-Forge — Phase 5B + 5C combined patch                            ║
# ║                                                                      ║
# ║  5B — Maintenance UI                                                 ║
# ║    • DELETE endpoints for runs/sessions/exports (cascading)          ║
# ║    • /api/v1/admin/disk-usage endpoint                               ║
# ║    • /api/v1/admin/cleanup endpoint (rejected iterations)            ║
# ║    • Maintenance page in UI showing disk usage + cleanup actions     ║
# ║    • Delete buttons on Runs / Sessions / Exports detail pages        ║
# ║                                                                      ║
# ║  5C — Content & Polish                                               ║
# ║    • 5 additional starter datasets in Qwen chat template format      ║
# ║    • Production README with badges + clear sections                  ║
# ║    • docs/SCREENSHOTS.md (what to capture)                           ║
# ║    • docs/DEMO_SCRIPT.md (2-min video walkthrough)                   ║
# ║                                                                      ║
# ║  Apply after Phase 4 is verified:                                    ║
# ║    chmod +x bootstrap_phase5bc.sh                                    ║
# ║    ./bootstrap_phase5bc.sh                                           ║
# ║    make rebuild   # picks up new DELETE/admin endpoints              ║
# ║    make dev                                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Applying Phase 5B + 5C patch..."

mkdir -p data/datasets/{code-review-helper,personal-email-assistant,recipe-extractor,medical-qa-rural-tn,customer-support-classifier}
mkdir -p apps/web/src/pages
mkdir -p apps/api/routers
mkdir -p docs

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  PHASE 5B — Maintenance API                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ─── apps/api/routers/admin.py — disk usage + cleanup ────────────────
cat > apps/api/routers/admin.py <<'EOF'
"""Maintenance endpoints: disk usage + cleanup sweeps."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from apps.api.models.export import Export
from apps.api.models.run import Run
from apps.api.models.session import SessionStatus, TrainingSession
from apps.api.services.db import get_session

router = APIRouter()

# Paths inside the API container map to host paths via docker-compose volumes
DATA_ROOT     = Path("/app/data")
RUNS_ROOT     = Path("/app/runs")
EXPORTS_ROOT  = Path("/app/exports")

SessionDep = Annotated[Session, Depends(get_session)]


class DiskUsage(BaseModel):
    label: str
    path: str
    bytes: int
    items: int


class DiskUsageResponse(BaseModel):
    entries: list[DiskUsage]
    total_bytes: int


def _dir_size(p: Path) -> tuple[int, int]:
    """Returns (total_bytes, item_count) — robust to permission errors."""
    total = 0
    items = 0
    if not p.exists():
        return 0, 0
    try:
        for sub in p.iterdir():
            if sub.is_symlink():
                continue
            items += 1
            if sub.is_file():
                try:
                    total += sub.stat().st_size
                except OSError:
                    pass
            elif sub.is_dir():
                sub_bytes, _ = _dir_size(sub)
                total += sub_bytes
    except OSError:
        pass
    return total, items


@router.get("/disk-usage", response_model=DiskUsageResponse)
def disk_usage() -> DiskUsageResponse:
    entries = []
    for label, path in [
        ("Runs",       RUNS_ROOT),
        ("Exports",    EXPORTS_ROOT),
        ("Datasets",   DATA_ROOT / "datasets"),
        ("Ingest staging", DATA_ROOT / ".ingest_staging"),
    ]:
        b, n = _dir_size(path)
        entries.append(DiskUsage(label=label, path=str(path), bytes=b, items=n))
    total = sum(e.bytes for e in entries)
    return DiskUsageResponse(entries=entries, total_bytes=total)


class CleanupPlan(BaseModel):
    rejected_runs: list[int]
    bytes_freed_estimate: int
    description: str


class CleanupResponse(BaseModel):
    deleted_run_ids: list[int]
    bytes_freed: int


@router.get("/cleanup/plan", response_model=CleanupPlan)
def cleanup_plan(db: SessionDep) -> CleanupPlan:
    """Show what 'cleanup rejected iterations' would delete WITHOUT touching anything."""
    rejected = _find_rejected_runs(db)
    bytes_estimate = 0
    for run_id in rejected:
        run_dir = RUNS_ROOT / str(run_id)
        if run_dir.exists():
            b, _ = _dir_size(run_dir)
            bytes_estimate += b
    return CleanupPlan(
        rejected_runs=rejected,
        bytes_freed_estimate=bytes_estimate,
        description=(
            "Will delete the on-disk artifacts (adapters, logs, configs) for "
            "rejected iterations of COMPLETED sessions only. The DB rows stay so "
            "you can still see the experiment history. Running sessions and "
            "winners are never touched. Exports are never touched."
        ),
    )


@router.post("/cleanup/execute", response_model=CleanupResponse)
def cleanup_execute(db: SessionDep) -> CleanupResponse:
    """Delete on-disk artifacts for rejected runs from completed sessions."""
    rejected = _find_rejected_runs(db)
    deleted = []
    bytes_freed = 0
    for run_id in rejected:
        run_dir = RUNS_ROOT / str(run_id)
        if not run_dir.exists():
            continue
        b, _ = _dir_size(run_dir)
        try:
            shutil.rmtree(run_dir)
            deleted.append(run_id)
            bytes_freed += b
        except OSError:
            continue
    return CleanupResponse(deleted_run_ids=deleted, bytes_freed=bytes_freed)


def _find_rejected_runs(db: Session) -> list[int]:
    """Find rejected runs (was_accepted=False) of completed sessions only.

    Excludes:
      • Runs from running sessions (still in progress)
      • Winners (was_accepted=True)
      • Standalone runs (session_id is None) — those are user-initiated, never auto-touch
      • Runs that have an Export (would orphan the export)
    """
    # Completed sessions
    sessions = list(db.exec(
        select(TrainingSession).where(TrainingSession.status == SessionStatus.COMPLETED)
    ).all())
    if not sessions:
        return []
    session_ids = [s.id for s in sessions]

    # Rejected runs in those sessions
    rejected = list(db.exec(
        select(Run).where(
            Run.session_id.in_(session_ids),
            Run.was_accepted == False,  # noqa: E712
        )
    ).all())

    # Filter out any with exports
    rejected_ids: list[int] = []
    for r in rejected:
        has_export = db.exec(
            select(Export).where(Export.run_id == r.id).limit(1)
        ).first()
        if not has_export:
            rejected_ids.append(r.id)
    return rejected_ids
EOF
echo "  ✓ apps/api/routers/admin.py"

# ─── DELETE endpoints on existing routers ────────────────────────────
# Append DELETE to runs
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/routers/runs.py")
text = p.read_text()
if "@router.delete" in text:
    print("  ✓ DELETE already in runs.py")
else:
    addition = '''

@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: int, session: SessionDep) -> None:
    """Delete a run and its metrics. Blocks if the run has exports."""
    from apps.api.models.export import Export
    import shutil
    from pathlib import Path

    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Block if exports exist
    exp = session.exec(select(Export).where(Export.run_id == run_id).limit(1)).first()
    if exp:
        raise HTTPException(
            409,
            f"Run #{run_id} has export #{exp.id}. Delete the export first.",
        )

    # Delete metrics (cascade)
    metrics_to_delete = session.exec(select(Metric).where(Metric.run_id == run_id)).all()
    for m in metrics_to_delete:
        session.delete(m)

    session.delete(run)
    session.commit()

    # Delete on-disk artifacts
    run_dir = Path("/app/runs") / str(run_id)
    if run_dir.exists():
        try:
            shutil.rmtree(run_dir)
        except OSError:
            pass
'''
    p.write_text(text + addition)
    print("  ✓ DELETE added to runs.py")
PYEOF

# Append DELETE to sessions
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/routers/sessions.py")
text = p.read_text()
if "@router.delete" in text:
    print("  ✓ DELETE already in sessions.py")
else:
    addition = '''

@router.delete("/{sid}", status_code=204)
def delete_session(sid: int, db: SessionDep) -> None:
    """Delete a session and ALL its child runs (cascading). Blocks if any child run has exports."""
    from apps.api.models.export import Export
    from apps.api.models.metric import Metric
    import shutil
    from pathlib import Path

    s = db.get(TrainingSession, sid)
    if not s:
        raise HTTPException(404, "Session not found")

    child_runs = list(db.exec(select(Run).where(Run.session_id == sid)).all())
    child_ids = [r.id for r in child_runs]

    # Block if any child has exports
    if child_ids:
        exp = db.exec(
            select(Export).where(Export.run_id.in_(child_ids)).limit(1)
        ).first()
        if exp:
            raise HTTPException(
                409,
                f"Session #{sid} has run #{exp.run_id} with export #{exp.id}. "
                "Delete that export first.",
            )

    # Cascade delete metrics → runs → session
    for r in child_runs:
        for m in list(db.exec(select(Metric).where(Metric.run_id == r.id)).all()):
            db.delete(m)
        db.delete(r)
        run_dir = Path("/app/runs") / str(r.id)
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except OSError:
                pass

    db.delete(s)
    db.commit()
'''
    p.write_text(text + addition)
    print("  ✓ DELETE added to sessions.py")
PYEOF

# Append DELETE to exports
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/routers/exports.py")
text = p.read_text()
if "@router.delete" in text:
    print("  ✓ DELETE already in exports.py")
else:
    addition = '''

@router.delete("/{xid}", status_code=204)
def delete_export(xid: int, db: SessionDep) -> None:
    """Delete an export and its on-disk artifacts."""
    import shutil
    from pathlib import Path

    e = db.get(Export, xid)
    if not e:
        raise HTTPException(404, "Export not found")

    db.delete(e)
    db.commit()

    export_dir = Path("/app/exports") / str(xid)
    if export_dir.exists():
        try:
            shutil.rmtree(export_dir)
        except OSError:
            pass
'''
    p.write_text(text + addition)
    print("  ✓ DELETE added to exports.py")
PYEOF

# Mount admin router in main.py
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/main.py")
text = p.read_text()

if "admin" in text and "admin.router" in text:
    print("  ✓ admin router already mounted")
else:
    # Update import line
    text = text.replace(
        "from apps.api.routers import datasets, exports, ingest, models, runs, sessions",
        "from apps.api.routers import admin, datasets, exports, ingest, models, runs, sessions",
    )
    # Add router mount
    text = text.replace(
        'app.include_router(exports.router, prefix="/api/v1/exports", tags=["exports"])',
        'app.include_router(exports.router, prefix="/api/v1/exports", tags=["exports"])\n'
        'app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])',
    )
    # Bump version
    text = text.replace('version="0.5.0"', 'version="0.6.0"')
    text = text.replace('"version": "0.5.0"', '"version": "0.6.0"')
    text = text.replace('version="0.5.0"', 'version="0.6.0"')
    text = text.replace('"Phase 4 — export to GGUF"', '"Phase 5 — maintenance + polish"')
    p.write_text(text)
    print("  ✓ admin router mounted in main.py")
PYEOF

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  UI — admin API client + Maintenance page + delete buttons          ║
# ╚══════════════════════════════════════════════════════════════════════╝

# Append admin API to lib/api.ts
cat >> apps/web/src/lib/api.ts <<'EOF'

// ─── Phase 5B admin / maintenance ────────────────────────────

export type DiskUsageEntry = {
  label: string;
  path: string;
  bytes: number;
  items: number;
};

export type DiskUsageResponse = {
  entries: DiskUsageEntry[];
  total_bytes: number;
};

export type CleanupPlan = {
  rejected_runs: number[];
  bytes_freed_estimate: number;
  description: string;
};

export type CleanupResponse = {
  deleted_run_ids: number[];
  bytes_freed: number;
};

async function jdelete(path: string): Promise<void> {
  const r = await fetch(`${API_URL}${path}`, { method: 'DELETE' });
  if (!r.ok && r.status !== 204) {
    let detail = '';
    try { detail = (await r.json()).detail ?? ''; } catch { /* ignore */ }
    throw new Error(`DELETE ${path} → HTTP ${r.status}${detail ? ` — ${detail}` : ''}`);
  }
}

export const admin = {
  diskUsage: () => jget<DiskUsageResponse>('/api/v1/admin/disk-usage'),
  cleanupPlan: () => jget<CleanupPlan>('/api/v1/admin/cleanup/plan'),
  cleanupExecute: () => jpost<CleanupResponse>('/api/v1/admin/cleanup/execute', {}),
};

// Add deletes to existing API objects
export const deletes = {
  run: (id: number) => jdelete(`/api/v1/runs/${id}`),
  session: (id: number) => jdelete(`/api/v1/sessions/${id}`),
  export: (id: number) => jdelete(`/api/v1/exports/${id}`),
};
EOF
echo "  ✓ admin + delete clients added to lib/api.ts"

# ─── Maintenance page ────────────────────────────────────────
cat > apps/web/src/pages/Maintenance.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { admin, type CleanupPlan, type DiskUsageResponse } from '../lib/api';

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let val = n / 1024;
  let u = 0;
  while (val >= 1024 && u < units.length - 1) {
    val /= 1024;
    u++;
  }
  return `${val.toFixed(val > 10 ? 0 : 1)} ${units[u]}`;
}

export default function Maintenance() {
  const [usage, setUsage] = useState<DiskUsageResponse | null>(null);
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [u, p] = await Promise.all([admin.diskUsage(), admin.cleanupPlan()]);
      setUsage(u);
      setPlan(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
    const iv = window.setInterval(refresh, 5000);
    return () => window.clearInterval(iv);
  }, []);

  async function doCleanup() {
    if (!plan || plan.rejected_runs.length === 0) return;
    if (!confirm(
      `Delete ${plan.rejected_runs.length} rejected iteration artifacts? ` +
      `Frees ~${humanBytes(plan.bytes_freed_estimate)}. DB rows are kept.`
    )) return;
    setBusy(true);
    try {
      const r = await admin.cleanupExecute();
      setLastResult(
        `✓ Deleted ${r.deleted_run_ids.length} runs · freed ${humanBytes(r.bytes_freed)}`
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Maintenance</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Disk usage and cleanup actions.
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>
      )}

      {lastResult && (
        <div className="rounded-md bg-emerald-950/50 px-3 py-2 text-sm text-emerald-300">
          {lastResult}
        </div>
      )}

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
          Disk usage
        </h3>
        {usage === null ? (
          <div className="text-sm text-zinc-500">Loading…</div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-zinc-500">
                <tr>
                  <th className="px-3 py-1.5 text-left">Location</th>
                  <th className="px-3 py-1.5 text-left font-mono">Path</th>
                  <th className="px-3 py-1.5 text-right">Items</th>
                  <th className="px-3 py-1.5 text-right">Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800">
                {usage.entries.map((e) => (
                  <tr key={e.label} className="font-mono text-zinc-300">
                    <td className="px-3 py-2">{e.label}</td>
                    <td className="px-3 py-2 text-xs text-zinc-500">{e.path}</td>
                    <td className="px-3 py-2 text-right text-zinc-400">{e.items}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{humanBytes(e.bytes)}</td>
                  </tr>
                ))}
                <tr className="border-t-2 border-zinc-700 font-mono">
                  <td className="px-3 py-2 font-semibold text-zinc-100" colSpan={3}>Total</td>
                  <td className="px-3 py-2 text-right text-emerald-400 tabular-nums">
                    {humanBytes(usage.total_bytes)}
                  </td>
                </tr>
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
          Cleanup: rejected iterations
        </h3>
        {plan === null ? (
          <div className="text-sm text-zinc-500">Loading…</div>
        ) : (
          <>
            <p className="mb-4 text-sm text-zinc-400">{plan.description}</p>
            {plan.rejected_runs.length === 0 ? (
              <div className="text-sm text-zinc-500">
                Nothing to clean up. (No rejected iterations from completed sessions found.)
              </div>
            ) : (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <div className="rounded-md border border-zinc-800 px-3 py-2">
                    <div className="font-mono text-xs text-zinc-500">candidates</div>
                    <div className="mt-1 font-mono text-lg text-zinc-100">
                      {plan.rejected_runs.length} runs
                    </div>
                  </div>
                  <div className="rounded-md border border-zinc-800 px-3 py-2">
                    <div className="font-mono text-xs text-zinc-500">estimated free</div>
                    <div className="mt-1 font-mono text-lg text-emerald-400">
                      {humanBytes(plan.bytes_freed_estimate)}
                    </div>
                  </div>
                </div>
                <details className="mb-3 text-xs">
                  <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300">
                    Show run IDs ({plan.rejected_runs.length})
                  </summary>
                  <div className="mt-2 font-mono text-zinc-400">
                    {plan.rejected_runs.join(', ')}
                  </div>
                </details>
                <button
                  onClick={doCleanup}
                  disabled={busy}
                  className="rounded-md bg-rose-700 px-4 py-2 text-sm font-medium text-white hover:bg-rose-600 disabled:cursor-not-allowed disabled:bg-zinc-700"
                >
                  {busy ? 'Cleaning…' : `Delete ${plan.rejected_runs.length} rejected iterations`}
                </button>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
EOF
echo "  ✓ apps/web/src/pages/Maintenance.tsx"

# ─── Update Nav.tsx + App.tsx to add Maintenance route ──────────────
python3 - <<'PYEOF'
from pathlib import Path

# Add to Nav
nav = Path("apps/web/src/components/Nav.tsx")
text = nav.read_text()
if "/maintenance" not in text:
    text = text.replace(
        '<NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : \'\'}`}>Datasets</NavLink>',
        '<NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : \'\'}`}>Datasets</NavLink>\n            <NavLink to="/maintenance" className={({ isActive }) => `${link} ${isActive ? activeLink : \'\'}`}>Maintenance</NavLink>',
    )
    nav.write_text(text)
    print("  ✓ Nav.tsx — added Maintenance link")
else:
    print("  ✓ Nav already has Maintenance link")

# Add to App.tsx
app = Path("apps/web/src/App.tsx")
text = app.read_text()
if "Maintenance" not in text:
    text = text.replace(
        "import Exports from './pages/Exports';",
        "import Exports from './pages/Exports';\nimport Maintenance from './pages/Maintenance';",
    )
    text = text.replace(
        '<Route path="/datasets/new" element={<NewDataset />} />',
        '<Route path="/datasets/new" element={<NewDataset />} />\n            <Route path="/maintenance" element={<Maintenance />} />',
    )
    app.write_text(text)
    print("  ✓ App.tsx — added /maintenance route")
else:
    print("  ✓ App already has /maintenance route")
PYEOF

# ─── Delete buttons on Runs / Sessions / Exports list pages ─────────
python3 - <<'PYEOF'
from pathlib import Path

# Runs.tsx — add delete column
p = Path("apps/web/src/pages/Runs.tsx")
text = p.read_text()

if "deletes.run" not in text:
    text = text.replace(
        "import { type Run, type RunStatus, api } from '../lib/api';",
        "import { type Run, type RunStatus, api, deletes } from '../lib/api';",
    )

    # Add delete handler and column
    text = text.replace(
        '<th className="px-4 py-2.5 text-right">Val loss</th>',
        '<th className="px-4 py-2.5 text-right">Val loss</th>\n                <th className="px-4 py-2.5 text-right"></th>',
    )

    # Find the last <td> in the row and add a delete button after it
    old_row = '''<td className="px-4 py-2.5 text-right">
                    {r.final_val_loss !== null ? r.final_val_loss.toFixed(3) : '—'}
                  </td>
                </tr>'''
    new_row = '''<td className="px-4 py-2.5 text-right">
                    {r.final_val_loss !== null ? r.final_val_loss.toFixed(3) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={async (e) => {
                        e.preventDefault();
                        if (!confirm(`Delete run #${r.id}? This also deletes its metrics and on-disk artifacts.`)) return;
                        try {
                          await deletes.run(r.id);
                        } catch (err) {
                          alert(err instanceof Error ? err.message : String(err));
                        }
                      }}
                      className="text-xs text-zinc-600 hover:text-rose-400"
                      title="Delete run"
                    >
                      delete
                    </button>
                  </td>
                </tr>'''
    text = text.replace(old_row, new_row)
    p.write_text(text)
    print("  ✓ Runs.tsx — added delete column")
else:
    print("  ✓ Runs already has delete column")

# Sessions.tsx — add delete column
p = Path("apps/web/src/pages/Sessions.tsx")
text = p.read_text()
if "deletes.session" not in text:
    text = text.replace(
        "import { type SessionStatus, type TrainingSession, api } from '../lib/api';",
        "import { type SessionStatus, type TrainingSession, api, deletes } from '../lib/api';",
    )
    text = text.replace(
        '<th className="px-4 py-2.5 text-right">Best metric</th>',
        '<th className="px-4 py-2.5 text-right">Best metric</th>\n                <th className="px-4 py-2.5 text-right"></th>',
    )
    old_row = '''<td className="px-4 py-2.5 text-right">
                    {s.best_metric_value !== null ? s.best_metric_value.toFixed(4) : '—'}
                  </td>
                </tr>'''
    new_row = '''<td className="px-4 py-2.5 text-right">
                    {s.best_metric_value !== null ? s.best_metric_value.toFixed(4) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={async (e) => {
                        e.preventDefault();
                        if (!confirm(`Delete session #${s.id} AND all its iteration runs?`)) return;
                        try {
                          await deletes.session(s.id);
                        } catch (err) {
                          alert(err instanceof Error ? err.message : String(err));
                        }
                      }}
                      className="text-xs text-zinc-600 hover:text-rose-400"
                      title="Delete session and all child runs"
                    >
                      delete
                    </button>
                  </td>
                </tr>'''
    text = text.replace(old_row, new_row)
    p.write_text(text)
    print("  ✓ Sessions.tsx — added delete column")
else:
    print("  ✓ Sessions already has delete column")

# Exports.tsx — add delete button to each export card
p = Path("apps/web/src/pages/Exports.tsx")
text = p.read_text()
if "deletes.export" not in text:
    text = text.replace(
        "import { type ExportRow, type ExportStatus, exportsApi } from '../lib/api';",
        "import { type ExportRow, type ExportStatus, exportsApi, deletes } from '../lib/api';",
    )
    # Inject a small delete link in the header line
    old_hdr = '<span className={`font-mono text-xs ${STATUS_STYLES[e.status]}`}>● {e.status}</span>'
    new_hdr = '''<div className="flex items-center gap-3">
                  <span className={`font-mono text-xs ${STATUS_STYLES[e.status]}`}>● {e.status}</span>
                  <button
                    onClick={async () => {
                      if (!confirm(`Delete export #${e.id}? This also removes the on-disk GGUF files.`)) return;
                      try {
                        await deletes.export(e.id);
                      } catch (err) {
                        alert(err instanceof Error ? err.message : String(err));
                      }
                    }}
                    className="text-xs text-zinc-600 hover:text-rose-400"
                    title="Delete export and GGUF files"
                  >
                    delete
                  </button>
                </div>'''
    text = text.replace(old_hdr, new_hdr)
    p.write_text(text)
    print("  ✓ Exports.tsx — added delete button")
else:
    print("  ✓ Exports already has delete button")
PYEOF

echo ""
echo "→ Phase 5B done. Continuing to 5C..."
echo ""

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  PHASE 5C — 5 sample datasets                                        ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ─── 1. code-review-helper ───────────────────────────────────────────
cat > data/datasets/code-review-helper/README.md <<'EOF'
# code-review-helper

Teaches the model to write code-review comments in a constructive, specific style.

Schema: Qwen2 chat template. 12 train · 2 valid.
EOF
cat > data/datasets/code-review-helper/train.jsonl <<'EOF'
{"text": "<|im_start|>user\n```python\ndef get_user(id):\n    users = db.query('SELECT * FROM users')\n    return [u for u in users if u.id == id]\n```\n\nReview this code.<|im_end|>\n<|im_start|>assistant\nThis fetches every row in the users table just to filter in Python — fine for 10 users, catastrophic at 100k. Use a WHERE clause: `db.query('SELECT * FROM users WHERE id = ?', id)`. Also returns a list when the caller wants a single user; consider `.first()` or returning None.<|im_end|>"}
{"text": "<|im_start|>user\n```js\nfunction calculateTotal(items) {\n  let total = 0;\n  for (let i = 0; i < items.length; i++) {\n    total = total + items[i].price * items[i].quantity;\n  }\n  return total;\n}\n```\n\nReview this code.<|im_end|>\n<|im_start|>assistant\nWorks but reads dated. Prefer `reduce`: `items.reduce((sum, item) => sum + item.price * item.quantity, 0)`. Also no guard against undefined `price` or `quantity` — one bad item produces NaN silently.<|im_end|>"}
{"text": "<|im_start|>user\n```python\ntry:\n    result = process(data)\nexcept Exception as e:\n    pass\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nBare except + pass is the single worst error-handling pattern. You're hiding ALL failures including KeyboardInterrupt and bugs you'd want to see. At minimum log the exception. Better: catch only the specific exception you expect, let everything else propagate.<|im_end|>"}
{"text": "<|im_start|>user\n```go\nfunc Add(a, b int) int {\n    return a + b\n}\n```\n\nThis is a simple utility. Anything to note?<|im_end|>\n<|im_start|>assistant\nThe code itself is fine. The only thing worth raising is whether you actually need it — `a + b` at the call site is one fewer indirection and doesn't require importing your package. Wrappers around language primitives usually only earn their keep with extra validation, logging, or type narrowing.<|im_end|>"}
{"text": "<|im_start|>user\n```python\nclass Order:\n    def __init__(self, items, customer, total, discount, tax, shipping_address, billing_address, payment_method, status, created_at):\n        self.items = items\n        ...\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\n10 positional args is a smell. Reading `Order([...], jane, 100, 0, 8, addr1, addr2, 'card', 'pending', now)` is impossible to verify by eye. Switch to a dataclass or keyword-only args. Also consider whether some of these belong on a separate `Payment` or `Shipping` object — current shape suggests Order is wearing too many hats.<|im_end|>"}
{"text": "<|im_start|>user\n```sql\nSELECT * FROM orders WHERE created_at > '2024-01-01';\n```\n\nThis query is slow on a 50M-row table. Why?<|im_end|>\n<|im_start|>assistant\nMost likely there's no index on `created_at`, so the engine is doing a full table scan. Verify with EXPLAIN. The fix is a B-tree index on `created_at`. Also: `SELECT *` pulls every column even if you only need a few — narrow it down, especially if the table has wide text columns.<|im_end|>"}
{"text": "<|im_start|>user\n```python\npasswords = {}\ndef store_password(user, pw):\n    passwords[user] = pw\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nStoring passwords in plaintext is a fireable offense even in a toy project — habits from toy projects are how real leaks happen. Use `bcrypt.hashpw(pw.encode(), bcrypt.gensalt())` and store the hash. Verify with `bcrypt.checkpw(...)`. Never roll your own crypto.<|im_end|>"}
{"text": "<|im_start|>user\n```tsx\nuseEffect(() => {\n  fetch('/api/data').then(r => r.json()).then(setData);\n}, []);\n```\n\nReview this React effect.<|im_end|>\n<|im_start|>assistant\nThree issues. (1) No error handling — failed fetches silently leave `data` undefined and components rendering forever. (2) No cleanup — if the component unmounts before the fetch returns, you'll get a state update on an unmounted component warning. (3) The empty deps array is fine for an initial fetch but document the intent. Add an AbortController and a try/catch.<|im_end|>"}
{"text": "<|im_start|>user\n```python\nfor i, item in enumerate(items):\n    items[i] = item.upper()\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nWorks but unnecessarily mutates the original list, which is a recipe for action-at-a-distance bugs. `items = [i.upper() for i in items]` is clearer about intent. If you genuinely need to mutate in place for memory reasons, leave a comment saying so.<|im_end|>"}
{"text": "<|im_start|>user\n```python\ndef divide(a, b):\n    return a / b\n```\n\nWhat's the failure mode here?<|im_end|>\n<|im_start|>assistant\nBlows up with ZeroDivisionError when b=0. Either accept that as a programmer error and let it propagate (often the right call), or define explicit behavior: raise ValueError with a clear message, or return None / math.inf depending on what your callers expect. Don't silently swallow it.<|im_end|>"}
{"text": "<|im_start|>user\n```python\nimport os\nfor f in os.listdir('/data'):\n    with open(f'/data/{f}') as fh:\n        process(fh.read())\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nLoading the entire file with `.read()` for every file in a directory is dangerous — one large file OOMs you. Use a streaming approach or read line-by-line. Also use `os.path.join` or `pathlib.Path` rather than f-string concatenation; the latter breaks on Windows and doesn't normalize paths.<|im_end|>"}
{"text": "<|im_start|>user\n```python\n# Get the user's age in years\nage = (datetime.now() - birthdate).days / 365\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nDividing days by 365 drifts: every 4 years you're off by a day, and after a decade your computed age can be wrong by 2-3 days. For people near birthday boundaries that's a real bug. Use dateutil's relativedelta or just compute year/month difference manually with a check on month-and-day.<|im_end|>"}
EOF
cat > data/datasets/code-review-helper/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\n```python\ndef get_config():\n    config = open('config.json').read()\n    return json.loads(config)\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nFile handle leak — `open()` without a context manager isn't guaranteed to close. Use `with open('config.json') as f: return json.load(f)` which also avoids the intermediate string. Also no error handling for missing file or malformed JSON; either let it propagate (probably right at startup) or give a clearer message than 'JSONDecodeError on line 47'.<|im_end|>"}
{"text": "<|im_start|>user\n```js\nasync function fetchAll(urls) {\n  const results = [];\n  for (const url of urls) {\n    results.push(await fetch(url));\n  }\n  return results;\n}\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nSequential awaits in a loop is the slowest way to fetch in parallel. Each URL waits for the previous to complete. Use `Promise.all(urls.map(url => fetch(url)))` to fire them concurrently. If you need backpressure (don't hammer one server with 1000 concurrent requests), use a pool like p-limit instead.<|im_end|>"}
EOF
cat > data/datasets/code-review-helper/canary.jsonl <<'EOF'
{"text": "<|im_start|>user\n```python\ndef cached_user(id):\n    if id in cache:\n        return cache[id]\n    result = db.query(id)\n    cache[id] = result\n    return result\n```\n\nReview.<|im_end|>\n<|im_start|>assistant\nThe pattern is fine but watch the unbounded growth — `cache` never evicts, so over time it accumulates every user ever queried. Use `functools.lru_cache` with a maxsize, or an explicit LRU. Also no thread safety; if this runs under threads you'll get races on the dict.<|im_end|>"}
EOF

# ─── 2. personal-email-assistant ─────────────────────────────────────
cat > data/datasets/personal-email-assistant/README.md <<'EOF'
# personal-email-assistant

Teaches the model your email-writing voice: direct, brief, no fluff.
Replace these with your own past emails to fine-tune to your actual style.

Schema: Qwen2 chat template. 10 train · 2 valid.
EOF
cat > data/datasets/personal-email-assistant/train.jsonl <<'EOF'
{"text": "<|im_start|>user\nDraft a reply: 'Hi Amit, can you make it to Thursday's review meeting at 3pm?'<|im_end|>\n<|im_start|>assistant\nYes, I'll be there.<|im_end|>"}
{"text": "<|im_start|>user\nDraft a reply to a recruiter: 'Are you open to new opportunities?'<|im_end|>\n<|im_start|>assistant\nThanks for reaching out — not actively looking right now. Happy to reconnect if something interesting comes up later in the year.<|im_end|>"}
{"text": "<|im_start|>user\nDraft a message to my team: I need to skip Friday's standup.<|im_end|>\n<|im_start|>assistant\nHeads up — skipping standup Friday, will catch up async in Slack. Anything urgent, ping me directly.<|im_end|>"}
{"text": "<|im_start|>user\nDraft a follow-up email three days after sending a proposal with no response.<|im_end|>\n<|im_start|>assistant\nQuick nudge on the proposal I sent Tuesday — let me know if you need any clarification or if a quick call would help.<|im_end|>"}
{"text": "<|im_start|>user\nDecline a coffee chat request from someone you don't know.<|im_end|>\n<|im_start|>assistant\nThanks for thinking of me. My calendar is full through the end of the month — feel free to send any specific questions over email and I'll reply when I can.<|im_end|>"}
{"text": "<|im_start|>user\nReply to your manager asking for a status on the Q2 deliverable.<|im_end|>\n<|im_start|>assistant\nOn track. Backend changes are deployed to staging; frontend integration ships Thursday. The one open risk is the analytics export — I'll know by tomorrow whether we need to slip that to next sprint.<|im_end|>"}
{"text": "<|im_start|>user\nAsk a colleague to review a PR before EOD.<|im_end|>\n<|im_start|>assistant\nWhen you get a sec — PR is up: <link>. Pretty small, ~80 lines. Need to merge it before EOD to unblock the release. Thanks.<|im_end|>"}
{"text": "<|im_start|>user\nReply 'no' politely to a vendor asking for a meeting to demo their product.<|im_end|>\n<|im_start|>assistant\nAppreciate the outreach. Not a fit for what we're building right now — please remove me from the demo cadence. Best of luck.<|im_end|>"}
{"text": "<|im_start|>user\nCongratulate a colleague on a promotion announcement.<|im_end|>\n<|im_start|>assistant\nNicely earned, congrats. Looking forward to working with you in the new role.<|im_end|>"}
{"text": "<|im_start|>user\nReply to your manager flagging that a deadline will slip by a week.<|im_end|>\n<|im_start|>assistant\nWanted to flag this early: the migration is going to slip by ~1 week. Underlying issue is the schema change has more dependencies than we estimated. Plan: I'll have a revised timeline by Friday. Let me know if you want to discuss live.<|im_end|>"}
EOF
cat > data/datasets/personal-email-assistant/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\nReply to a customer asking 'When will feature X ship?' when you don't have a date yet.<|im_end|>\n<|im_start|>assistant\nNot a firm date yet — it's in the next quarter's planning. I'll loop back when we lock it in. Anything I can share with you on the current behavior to bridge the gap?<|im_end|>"}
{"text": "<|im_start|>user\nDraft an email apologizing for missing yesterday's meeting.<|im_end|>\n<|im_start|>assistant\nSorry for missing yesterday — got pulled into an incident and lost track. Can I get a quick recap, or jump on a 15-min sync to catch up?<|im_end|>"}
EOF

# ─── 3. recipe-extractor ─────────────────────────────────────────────
cat > data/datasets/recipe-extractor/README.md <<'EOF'
# recipe-extractor

Extracts structured recipe data (title, ingredients, steps) from messy text input.

Schema: Qwen2 chat template. 10 train · 2 valid.
EOF
cat > data/datasets/recipe-extractor/train.jsonl <<'EOF'
{"text": "<|im_start|>user\nExtract structured JSON from this recipe text:\n\nGrandma's Pancakes — Mix 2 cups flour, 2 tbsp sugar, 1 tsp baking powder, 1/2 tsp salt. Whisk in 2 eggs, 1.5 cups milk, 3 tbsp melted butter. Cook on hot griddle until bubbles form, flip. Serves 4.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Grandma's Pancakes\", \"servings\": 4, \"ingredients\": [{\"item\": \"flour\", \"amount\": \"2 cups\"}, {\"item\": \"sugar\", \"amount\": \"2 tbsp\"}, {\"item\": \"baking powder\", \"amount\": \"1 tsp\"}, {\"item\": \"salt\", \"amount\": \"1/2 tsp\"}, {\"item\": \"eggs\", \"amount\": \"2\"}, {\"item\": \"milk\", \"amount\": \"1.5 cups\"}, {\"item\": \"melted butter\", \"amount\": \"3 tbsp\"}], \"steps\": [\"Mix flour, sugar, baking powder, and salt\", \"Whisk in eggs, milk, and melted butter\", \"Cook on hot griddle until bubbles form\", \"Flip and cook other side\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract JSON: Quick guacamole. Mash 3 ripe avocados, mix with juice of 1 lime, 1/4 cup chopped cilantro, 1 minced garlic clove, salt to taste. Serves 6.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Quick Guacamole\", \"servings\": 6, \"ingredients\": [{\"item\": \"ripe avocados\", \"amount\": \"3\"}, {\"item\": \"lime juice\", \"amount\": \"1 lime\"}, {\"item\": \"chopped cilantro\", \"amount\": \"1/4 cup\"}, {\"item\": \"minced garlic clove\", \"amount\": \"1\"}, {\"item\": \"salt\", \"amount\": \"to taste\"}], \"steps\": [\"Mash avocados\", \"Mix in lime juice, cilantro, garlic, and salt\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: One-pot pasta. Bring 4 cups water to boil, add 1 lb spaghetti, 1 can diced tomatoes, 4 garlic cloves, 1 tsp salt, drizzle olive oil. Simmer 10 min stirring. Top with parmesan. Serves 4.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"One-Pot Pasta\", \"servings\": 4, \"ingredients\": [{\"item\": \"water\", \"amount\": \"4 cups\"}, {\"item\": \"spaghetti\", \"amount\": \"1 lb\"}, {\"item\": \"diced tomatoes\", \"amount\": \"1 can\"}, {\"item\": \"garlic cloves\", \"amount\": \"4\"}, {\"item\": \"salt\", \"amount\": \"1 tsp\"}, {\"item\": \"olive oil\", \"amount\": \"drizzle\"}, {\"item\": \"parmesan\", \"amount\": \"for topping\"}], \"steps\": [\"Bring water to boil\", \"Add spaghetti, tomatoes, garlic, salt, and olive oil\", \"Simmer 10 minutes, stirring\", \"Top with parmesan\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Banana bread. Cream 1/2 cup butter with 1 cup sugar. Add 2 eggs. Mash 3 ripe bananas, mix in. Stir in 2 cups flour, 1 tsp baking soda, pinch of salt. Bake at 350F for 60 min. Makes 1 loaf.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Banana Bread\", \"servings\": \"1 loaf\", \"ingredients\": [{\"item\": \"butter\", \"amount\": \"1/2 cup\"}, {\"item\": \"sugar\", \"amount\": \"1 cup\"}, {\"item\": \"eggs\", \"amount\": \"2\"}, {\"item\": \"ripe bananas\", \"amount\": \"3\"}, {\"item\": \"flour\", \"amount\": \"2 cups\"}, {\"item\": \"baking soda\", \"amount\": \"1 tsp\"}, {\"item\": \"salt\", \"amount\": \"pinch\"}], \"steps\": [\"Cream butter with sugar\", \"Add eggs\", \"Mash bananas and mix in\", \"Stir in flour, baking soda, and salt\", \"Bake at 350F for 60 minutes\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Simple vinaigrette. Whisk together 3 parts olive oil, 1 part vinegar, 1 tsp dijon, salt and pepper.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Simple Vinaigrette\", \"servings\": null, \"ingredients\": [{\"item\": \"olive oil\", \"amount\": \"3 parts\"}, {\"item\": \"vinegar\", \"amount\": \"1 part\"}, {\"item\": \"dijon\", \"amount\": \"1 tsp\"}, {\"item\": \"salt\", \"amount\": \"to taste\"}, {\"item\": \"pepper\", \"amount\": \"to taste\"}], \"steps\": [\"Whisk all ingredients together\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Roast chicken thighs at 425F for 35 min with 2 lbs chicken thighs, 4 minced garlic cloves, 2 tbsp olive oil, fresh rosemary, salt and pepper. Serves 4.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Roast Chicken Thighs\", \"servings\": 4, \"ingredients\": [{\"item\": \"chicken thighs\", \"amount\": \"2 lbs\"}, {\"item\": \"minced garlic cloves\", \"amount\": \"4\"}, {\"item\": \"olive oil\", \"amount\": \"2 tbsp\"}, {\"item\": \"fresh rosemary\", \"amount\": \"to taste\"}, {\"item\": \"salt\", \"amount\": \"to taste\"}, {\"item\": \"pepper\", \"amount\": \"to taste\"}], \"steps\": [\"Toss chicken thighs with garlic, olive oil, rosemary, salt, and pepper\", \"Roast at 425F for 35 minutes\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Caprese — slice 2 large tomatoes and 8oz fresh mozzarella, layer with basil leaves, drizzle olive oil and balsamic, salt and pepper. Serves 4.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Caprese\", \"servings\": 4, \"ingredients\": [{\"item\": \"large tomatoes\", \"amount\": \"2\"}, {\"item\": \"fresh mozzarella\", \"amount\": \"8oz\"}, {\"item\": \"basil leaves\", \"amount\": \"a handful\"}, {\"item\": \"olive oil\", \"amount\": \"drizzle\"}, {\"item\": \"balsamic\", \"amount\": \"drizzle\"}, {\"item\": \"salt\", \"amount\": \"to taste\"}, {\"item\": \"pepper\", \"amount\": \"to taste\"}], \"steps\": [\"Slice tomatoes and mozzarella\", \"Layer with basil leaves\", \"Drizzle olive oil and balsamic\", \"Season with salt and pepper\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Cold brew. Combine 1 cup coarse-ground coffee with 4 cups cold water, steep 12 hours in fridge, strain. Makes 4 servings.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Cold Brew\", \"servings\": 4, \"ingredients\": [{\"item\": \"coarse-ground coffee\", \"amount\": \"1 cup\"}, {\"item\": \"cold water\", \"amount\": \"4 cups\"}], \"steps\": [\"Combine coffee and cold water\", \"Steep 12 hours in fridge\", \"Strain\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Quick stir-fry. Heat 1 tbsp sesame oil, add 1 lb chicken cubes, cook 5 min. Add 3 cups mixed veggies, 3 tbsp soy sauce, 1 tbsp honey, 1 tsp ginger. Stir-fry 5 more min. Serve over rice. 4 servings.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Quick Stir-Fry\", \"servings\": 4, \"ingredients\": [{\"item\": \"sesame oil\", \"amount\": \"1 tbsp\"}, {\"item\": \"chicken cubes\", \"amount\": \"1 lb\"}, {\"item\": \"mixed veggies\", \"amount\": \"3 cups\"}, {\"item\": \"soy sauce\", \"amount\": \"3 tbsp\"}, {\"item\": \"honey\", \"amount\": \"1 tbsp\"}, {\"item\": \"ginger\", \"amount\": \"1 tsp\"}, {\"item\": \"rice\", \"amount\": \"for serving\"}], \"steps\": [\"Heat sesame oil\", \"Add chicken cubes and cook 5 minutes\", \"Add veggies, soy sauce, honey, and ginger\", \"Stir-fry 5 more minutes\", \"Serve over rice\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Garlic bread. Slice baguette lengthwise. Mix 1/2 cup softened butter, 4 minced garlic cloves, 2 tbsp chopped parsley, pinch salt. Spread on bread, broil 3-5 min until golden. Serves 6.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Garlic Bread\", \"servings\": 6, \"ingredients\": [{\"item\": \"baguette\", \"amount\": \"1\"}, {\"item\": \"softened butter\", \"amount\": \"1/2 cup\"}, {\"item\": \"minced garlic cloves\", \"amount\": \"4\"}, {\"item\": \"chopped parsley\", \"amount\": \"2 tbsp\"}, {\"item\": \"salt\", \"amount\": \"pinch\"}], \"steps\": [\"Slice baguette lengthwise\", \"Mix butter, garlic, parsley, and salt\", \"Spread on bread\", \"Broil 3-5 minutes until golden\"]}<|im_end|>"}
EOF
cat > data/datasets/recipe-extractor/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\nExtract: French toast. Whisk 4 eggs, 1/2 cup milk, 1 tsp vanilla, 1/2 tsp cinnamon. Dip 8 slices bread, cook in buttered pan 2-3 min per side. Serve with maple syrup. 4 servings.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"French Toast\", \"servings\": 4, \"ingredients\": [{\"item\": \"eggs\", \"amount\": \"4\"}, {\"item\": \"milk\", \"amount\": \"1/2 cup\"}, {\"item\": \"vanilla\", \"amount\": \"1 tsp\"}, {\"item\": \"cinnamon\", \"amount\": \"1/2 tsp\"}, {\"item\": \"bread slices\", \"amount\": \"8\"}, {\"item\": \"butter\", \"amount\": \"for pan\"}, {\"item\": \"maple syrup\", \"amount\": \"for serving\"}], \"steps\": [\"Whisk eggs, milk, vanilla, and cinnamon\", \"Dip bread slices in mixture\", \"Cook in buttered pan 2-3 minutes per side\", \"Serve with maple syrup\"]}<|im_end|>"}
{"text": "<|im_start|>user\nExtract: Hummus. Blend 1 can chickpeas (drained), 1/4 cup tahini, juice of 1 lemon, 2 garlic cloves, 2 tbsp olive oil, 1/2 tsp salt, 2-3 tbsp water for consistency. Serves 6.<|im_end|>\n<|im_start|>assistant\n{\"title\": \"Hummus\", \"servings\": 6, \"ingredients\": [{\"item\": \"chickpeas, drained\", \"amount\": \"1 can\"}, {\"item\": \"tahini\", \"amount\": \"1/4 cup\"}, {\"item\": \"lemon juice\", \"amount\": \"1 lemon\"}, {\"item\": \"garlic cloves\", \"amount\": \"2\"}, {\"item\": \"olive oil\", \"amount\": \"2 tbsp\"}, {\"item\": \"salt\", \"amount\": \"1/2 tsp\"}, {\"item\": \"water\", \"amount\": \"2-3 tbsp\"}], \"steps\": [\"Blend all ingredients\", \"Adjust water for consistency\"]}<|im_end|>"}
EOF

# ─── 4. medical-qa-rural-tn (English/Tamil hybrid) ───────────────────
cat > data/datasets/medical-qa-rural-tn/README.md <<'EOF'
# medical-qa-rural-tn

Domain Q&A localized to rural Tamil Nadu primary-care context.
English questions, plain-English answers with regional terminology where helpful.

⚠ NOT medical advice. For pipeline-validation purposes only.
A real version requires medical professional curation.

Schema: Qwen2 chat template. 10 train · 2 valid.
EOF
cat > data/datasets/medical-qa-rural-tn/train.jsonl <<'EOF'
{"text": "<|im_start|>user\nWhat are the warning signs of dengue I should watch for in my child?<|im_end|>\n<|im_start|>assistant\nHigh fever (often 39-40C), severe headache, pain behind the eyes, joint and muscle aches, rash. The danger signs that mean go to a hospital immediately are: severe abdominal pain, persistent vomiting, bleeding from gums or nose, restlessness, or any drop in temperature combined with worsening tiredness. These suggest dengue is becoming severe. Do not give aspirin or ibuprofen — only paracetamol for fever. Keep the child hydrated.<|im_end|>"}
{"text": "<|im_start|>user\nMy elderly father has diabetes and a wound on his foot that isn't healing. How urgent is this?<|im_end|>\n<|im_start|>assistant\nDiabetic foot wounds are urgent. Even small wounds can progress to serious infection or amputation if neglected. Go to a doctor within 24-48 hours, sooner if there's any redness spreading, foul smell, pus, or fever. In the meantime: clean with saline, keep dry, no walking on it. Do not use traditional poultices on diabetic wounds — they can hide infection.<|im_end|>"}
{"text": "<|im_start|>user\nIs it safe to take antibiotics from the pharmacy without a prescription if I have a sore throat?<|im_end|>\n<|im_start|>assistant\nNo. Most sore throats are viral and antibiotics do nothing for them — taking them anyway contributes to resistance, which is a serious problem across India. If you have fever above 38.5C, white patches on tonsils, or pain when swallowing for more than 3 days, see a doctor for diagnosis. They may do a quick test. Save antibiotics for when you actually need them.<|im_end|>"}
{"text": "<|im_start|>user\nMy pregnant wife is in her 7th month. She has swelling in her feet. Should we be worried?<|im_end|>\n<|im_start|>assistant\nMild swelling in feet and ankles is common in the third trimester and usually not dangerous. Worrying signs that need immediate medical attention: severe headache, blurred vision, sudden swelling in face or hands, pain in upper abdomen, or very high blood pressure. These can be signs of preeclampsia which is serious. Keep her feet elevated when sitting, walk daily, drink water, and don't skip antenatal checkups.<|im_end|>"}
{"text": "<|im_start|>user\nMy 5-year-old has had loose stools for two days. When should I take him to the doctor?<|im_end|>\n<|im_start|>assistant\nDehydration is the biggest risk in children. Take him to the doctor today if: he hasn't urinated in 8 hours, his eyes look sunken, he's lethargic or won't drink, there's blood in the stools, or he has high fever. For mild diarrhea without these signs: give ORS (oral rehydration salts) frequently, continue normal feeding, avoid sugary drinks. If diarrhea continues past 3 days even without danger signs, see a doctor.<|im_end|>"}
{"text": "<|im_start|>user\nWhat is the right way to treat a snake bite while traveling to the hospital?<|im_end|>\n<|im_start|>assistant\nKeep the victim calm and still — movement spreads the venom faster. Do not cut the wound, do not suck out venom, do not apply a tight tourniquet, do not give alcohol or food. Remove rings or tight clothing near the bite. Keep the bitten limb at or below heart level. Get to the nearest hospital with antivenom availability as fast as possible — every minute matters. If possible, note the snake's appearance but do not try to catch or kill it.<|im_end|>"}
{"text": "<|im_start|>user\nHow do I know if I have heat stroke vs just being tired from heat?<|im_end|>\n<|im_start|>assistant\nHeat exhaustion is heavy sweating, weakness, nausea, headache, fast pulse — body temp around 38-39C. Move to a cool place, sip water with salt, rest. Heat STROKE is much worse: body temp above 40C, hot and DRY skin (often sweating stops), confusion or fainting. This is a medical emergency. Cool the person immediately — wet sheets, cold packs to neck and armpits — and get to a hospital. Heat stroke can be fatal within hours.<|im_end|>"}
{"text": "<|im_start|>user\nMy mother has high blood pressure and her medicine is finished. Can she skip it for a few days until I can get more?<|im_end|>\n<|im_start|>assistant\nNo, please don't skip BP medicine. Sudden stopping can cause a rebound — blood pressure jumps very high, raising risk of stroke. Even one or two missed doses is risky for some people. Call the doctor, the pharmacy, or visit the PHC the same day. Most BP medicines are cheap and available at government health centers under the free drug program. Don't borrow someone else's BP medicine — different ones have different uses.<|im_end|>"}
{"text": "<|im_start|>user\nI saw blood in my urine but I have no pain. Should I worry?<|im_end|>\n<|im_start|>assistant\nYes, see a doctor within a week even though there's no pain. Painless blood in urine can be from kidney stones, infection, or sometimes more serious causes — it shouldn't be ignored. A simple urine test plus an ultrasound at the PHC will usually clarify. If you also have fever, severe back pain, or can't pass urine, go immediately.<|im_end|>"}
{"text": "<|im_start|>user\nWhat should I do if someone is having a seizure?<|im_end|>\n<|im_start|>assistant\nMove sharp or hard objects away. Cushion their head with something soft. Loosen tight clothing around the neck. DO NOT put anything in their mouth — they will not swallow their tongue, but they can bite your fingers or break their teeth. Time the seizure. Most last under 2 minutes. After it ends, turn them on their side to keep the airway clear. Call for help and get them to a hospital, especially if it's their first seizure or it lasts more than 5 minutes.<|im_end|>"}
EOF
cat > data/datasets/medical-qa-rural-tn/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\nMy child has been coughing for a month. Should I be worried about tuberculosis?<|im_end|>\n<|im_start|>assistant\nA cough lasting more than 2-3 weeks deserves a doctor's evaluation. TB is a real concern in many parts of India and is treatable but requires diagnosis. Other signs that raise suspicion: weight loss, evening fever, night sweats, decreased appetite, or contact with someone who has TB. Take the child to the PHC — TB testing and treatment are free under the government program. Early diagnosis means easier treatment and prevents spread.<|im_end|>"}
{"text": "<|im_start|>user\nMy father is 65 and his BP has been 160/100 for a few weeks but he says he feels fine. Does he need treatment?<|im_end|>\n<|im_start|>assistant\nYes, even when there are no symptoms. 160/100 is stage 2 hypertension and over time causes silent damage to heart, kidneys, and brain — stroke and heart attack are the main risks. Many people feel fine until something serious happens. Take him to a doctor within the week. Treatment is usually one or two daily tablets, lifestyle changes (less salt, walking, weight loss if needed), and regular BP checks. The medicines are very effective.<|im_end|>"}
EOF

# ─── 5. customer-support-classifier ──────────────────────────────────
cat > data/datasets/customer-support-classifier/README.md <<'EOF'
# customer-support-classifier

Classify incoming support tickets into categories.
Output is a single short label — tests classification fine-tuning (not generation).

Categories: billing, technical, account, refund, feature_request, complaint
Schema: Qwen2 chat template. 12 train · 2 valid.
EOF
cat > data/datasets/customer-support-classifier/train.jsonl <<'EOF'
{"text": "<|im_start|>user\nClassify this ticket into one of: billing, technical, account, refund, feature_request, complaint.\n\nTicket: I was charged twice for my subscription last month, can you check?<|im_end|>\n<|im_start|>assistant\nbilling<|im_end|>"}
{"text": "<|im_start|>user\nClassify: My password reset email isn't arriving even though I requested it three times.<|im_end|>\n<|im_start|>assistant\naccount<|im_end|>"}
{"text": "<|im_start|>user\nClassify: The app crashes every time I try to upload a photo. iPhone 14, latest iOS.<|im_end|>\n<|im_start|>assistant\ntechnical<|im_end|>"}
{"text": "<|im_start|>user\nClassify: I never received the package and I want my money back.<|im_end|>\n<|im_start|>assistant\nrefund<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Would love to see dark mode in the next update — eyes are killing me at night.<|im_end|>\n<|im_start|>assistant\nfeature_request<|im_end|>"}
{"text": "<|im_start|>user\nClassify: This is the third time my order has been delayed without explanation. Honestly disappointed.<|im_end|>\n<|im_start|>assistant\ncomplaint<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Trying to change the email associated with my account but the change link expires before I can click it.<|im_end|>\n<|im_start|>assistant\naccount<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Why does my receipt show $79 when checkout said $69?<|im_end|>\n<|im_start|>assistant\nbilling<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Login button is unresponsive on Firefox. Works on Chrome.<|im_end|>\n<|im_start|>assistant\ntechnical<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Please cancel my order and refund me, I changed my mind before it shipped.<|im_end|>\n<|im_start|>assistant\nrefund<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Any chance you'll support CSV export of transaction history? Currently can't get my data out.<|im_end|>\n<|im_start|>assistant\nfeature_request<|im_end|>"}
{"text": "<|im_start|>user\nClassify: I've been a customer for five years and your support response time has gotten ridiculous.<|im_end|>\n<|im_start|>assistant\ncomplaint<|im_end|>"}
EOF
cat > data/datasets/customer-support-classifier/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\nClassify: My subscription renewed even though I cancelled last week. Refund please.<|im_end|>\n<|im_start|>assistant\nrefund<|im_end|>"}
{"text": "<|im_start|>user\nClassify: Two-factor authentication code never arrives over SMS. Stuck out of my account.<|im_end|>\n<|im_start|>assistant\naccount<|im_end|>"}
EOF

echo "  ✓ 5 datasets written"

# ─── 5C: README + docs ──────────────────────────────────────────────
cat > README.md <<'EOF'
# SLM-Forge

> Local-first fine-tuning lab for small language models on Apple Silicon. Hermes Agent drives autoresearch. One-click export to iPhone via PocketPal.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Built for Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-MLX-orange.svg)](https://github.com/ml-explore/mlx)

---

## What this is

A complete pipeline for fine-tuning small language models (Qwen 2.5 3B, Llama 3.2 3B) on your MacBook Pro, with a Hermes-agent-driven autoresearch loop that automatically explores hyperparameters, and a one-click GGUF export so you can run your fine-tuned model on your iPhone offline.

Built specifically for M3 Max with 36GB unified memory. Smaller Apple Silicon Macs work too with reduced model sizes.

## What it does

| Capability | Status |
|---|---|
| LoRA / DoRA / full SFT on Apple Silicon via MLX | ✓ |
| Autoresearch ratchet (Hermes-driven hyperparameter sweeps) | ✓ |
| Live training metrics + ratchet timeline graphs | ✓ |
| Data ingestion: upload, URL, web scrape, S3 | ✓ |
| Export to GGUF + quantize for iPhone | ✓ |
| Maintenance UI (disk usage, cleanup) | ✓ |
| 6 starter datasets (stock-analyst, code-review, email, recipes, medical QA, support classifier) | ✓ |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    macOS Host (M3 Max)                           │
│                                                                  │
│   ┌──────────┐    ┌──────────┐                                   │
│   │ React UI │───▶│ FastAPI  │ ← Docker                          │
│   └──────────┘    └────┬─────┘                                   │
│                        │ SQLite + Huey queue                     │
│                        │                                         │
│   ┌────────────────────▼─────────────────────────┐               │
│   │ Trainer  │ Ratchet  │ Exporter │ Hermes      │ ← host procs  │
│   │ (MLX-LM) │ (loop)   │ (GGUF)   │ Bridge      │   (Metal)     │
│   └──────────┴──────────┴──────────┴──────┬──────┘               │
│                                            │                     │
│   ┌────────────────────────────────────────▼────┐                │
│   │ Ollama : qwen3:30b-a3b (or any model)       │                │
│   └─────────────────────────────────────────────┘                │
└────────────────────────┬─────────────────────────────────────────┘
                         │ GGUF transfer
                         ▼
                  ┌─────────────┐
                  │   iPhone    │
                  │ PocketPal AI│
                  └─────────────┘
```

See `docs/ARCHITECTURE.md` for the full architecture write-up.

## Requirements

- macOS on Apple Silicon (M1/M2/M3 — M3 Max with 36GB unified memory is the development target)
- Python 3.12 or 3.13
- Node.js 20+
- Homebrew
- Docker Desktop for Mac
- ~30 GB free disk for models + exports

## Quick start

```bash
# 1. Clone
git clone git@github.com:<you>/slm_forge_hermes_integration.git
cd slm_forge_hermes_integration

# 2. One-time setup
make setup                    # uv + Python + Node deps
make install-hermes           # Ollama + qwen3:30b-a3b
brew install llama.cpp        # GGUF tooling

# 3. Start everything (four terminals)
make dev                      # T1: UI on :5173, API on :8000
make trainer                  # T2: LoRA training worker
make ratchet                  # T3: autoresearch loop
make exporter                 # T4: GGUF export worker

# 4. Open the UI
open http://localhost:5173
```

## End-to-end walkthrough

```
1. Ingest a dataset            → /datasets/new
2. Start an autoresearch run   → /sessions/new
3. Watch the ratchet graph     → /sessions/:id
4. Export the winner to GGUF   → /runs/:id → "Export to GGUF"
5. Download Q4_K_M.gguf        → /exports
6. AirDrop to iPhone           → PocketPal AI → Add Local Model
```

## Documentation

- `docs/ARCHITECTURE.md` — why this architecture (and what we rejected)
- `docs/SETUP.md` — detailed setup, troubleshooting
- `docs/IPHONE_DEPLOY.md` — getting your model onto iPhone
- `docs/DEMO_SCRIPT.md` — 2-minute video walkthrough
- `docs/SCREENSHOTS.md` — UI captures (what to record)

## What's intentionally NOT here

- ❌ Kubernetes / ArgoCD — single-machine tool, no cluster
- ❌ Auth — local-only, single-user
- ❌ Multi-GPU — Apple Silicon unified memory only
- ❌ RLHF PPO — DPO works, full PPO needs cluster
- ❌ Production monitoring — this is a personal lab

## License

MIT
EOF
echo "  ✓ README.md (production version)"

cat > docs/SCREENSHOTS.md <<'EOF'
# Screenshots to capture

A walkthrough of the UI for documentation and demos. Capture these in order and save to `docs/screenshots/`.

## 1. Dashboard (`http://localhost:5173/`)
   - Filename: `01-dashboard.png`
   - Shows capability matrix (all green after Phase 4)

## 2. New Session form (`/sessions/new`)
   - Filename: `02-new-session.png`
   - Hyperparameters configured, dataset = stock-analyst

## 3. Session detail with ratchet graph (`/sessions/:id` while running)
   - Filename: `03-ratchet-running.png`
   - At least 3-4 iterations done so you see green + red dots

## 4. Live loss curves on a single run (`/runs/:id` during training)
   - Filename: `04-live-loss.png`
   - Train + val loss curves both rendered

## 5. Dataset ingestion wizard step 1 (`/datasets/new`)
   - Filename: `05-ingest-step1.png`
   - "Upload file" tab selected

## 6. Dataset ingestion wizard step 2 (preview + schema mapping)
   - Filename: `06-ingest-step2.png`
   - After uploading sample.jsonl, fields detected, ready to save

## 7. Exports page with completed export (`/exports`)
   - Filename: `07-exports.png`
   - Q4_K_M tile visible with size + download link

## 8. Maintenance page (`/maintenance`)
   - Filename: `08-maintenance.png`
   - Disk usage table + cleanup action

## Capture tips

- macOS: `Cmd+Shift+5` → "Capture Selected Window" for clean shots
- Use Safari or Chrome at the default zoom (no scaling)
- Window width ~1280 px works well for README embedding
- After capture: `pngquant docs/screenshots/*.png --ext .png --force` to compress
EOF
echo "  ✓ docs/SCREENSHOTS.md"

cat > docs/DEMO_SCRIPT.md <<'EOF'
# 2-minute demo video script

Use any screen recorder (QuickTime works fine). Keep it under 2:30. Voiceover is optional but adds 30% to impact.

## Setup before recording

- All four terminals running (`make dev`, `make trainer`, `make ratchet`, `make exporter`)
- Browser at http://localhost:5173/
- Database NOT empty — have at least one completed run already
- iPhone next to your screen with PocketPal AI open

## Timing breakdown

### 0:00–0:15 — Title + premise (15s)
> "This is SLM-Forge. It fine-tunes small language models on a MacBook and deploys them to iPhone. All offline."

Show: Dashboard at `/`. Read the tagline aloud.

### 0:15–0:30 — Data in (15s)
> "Ingest your data from anywhere — file, URL, web scrape, or S3."

Show: Click "+ Dataset" → flip through the four source tabs → upload a small JSONL → land on the preview screen.

### 0:30–1:00 — Train + autoresearch (30s)
> "Start a session. Hermes Agent — running on local Ollama — proposes hyperparameter mutations. The ratchet keeps improvements, discards regressions. No PyTorch, no CUDA — pure MLX."

Show: Navigate to a running session → ratchet timeline graph descending → zoom on the green/red dots → switch to a single run page showing live loss curves.

### 1:00–1:30 — Export to GGUF (30s)
> "Click 'Export to GGUF'. Behind the scenes: LoRA fuses into the base, converts to GGUF, quantizes to Q4_K_M and Q8_0."

Show: Run detail page → click "Export to GGUF →" → land on /exports showing progress → completed state.

### 1:30–2:00 — iPhone deployment (30s)
> "AirDrop the Q4_K_M file to your iPhone. Open PocketPal AI. Add local model. Done. Fully offline."

Show: AirDrop dialog → PocketPal "Add Local Model" → loaded model → chat reply appearing.

### 2:00–2:20 — Maintenance + close (20s)
> "Disk usage and cleanup are built in. Open source, MIT licensed."

Show: /maintenance page briefly → GitHub URL on screen at the end.

## Voice / tone

Direct, factual, no hype words ("revolutionary", "game-changing"). The product is technical enough that overselling makes it less credible.

## Music

Optional. If used, keep it under -20dB so voice stays primary.
EOF
echo "  ✓ docs/DEMO_SCRIPT.md"

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Bump version + summary                                              ║
# ╚══════════════════════════════════════════════════════════════════════╝

python3 - <<'PYEOF'
from pathlib import Path
p = Path("pyproject.toml")
text = p.read_text()
text = text.replace('version = "0.5.0"', 'version = "0.6.0"')
p.write_text(text)
print("  ✓ pyproject.toml bumped to 0.6.0")
PYEOF

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Phase 5B + 5C patch applied                                       ║
╚══════════════════════════════════════════════════════════════════════╝

Phase 5B — Maintenance:
  • DELETE endpoints     /api/v1/{runs,sessions,exports}/{id}
  • Admin API            /api/v1/admin/{disk-usage,cleanup/plan,cleanup/execute}
  • UI: /maintenance     disk usage table + cleanup action
  • UI: delete buttons   on Runs, Sessions, Exports tables

Phase 5C — Content:
  • 5 new datasets:      code-review-helper, personal-email-assistant,
                         recipe-extractor, medical-qa-rural-tn,
                         customer-support-classifier
                         (10-15 examples each, Qwen chat template)
  • README.md            production-grade with badges, architecture, quickstart
  • docs/SCREENSHOTS.md  what to capture and where
  • docs/DEMO_SCRIPT.md  2-minute video walkthrough

Now:

  make rebuild         # picks up new DELETE/admin routers in API container
  make dev

Then visit:
  http://localhost:5173/maintenance        ← new page
  http://localhost:5173/datasets           ← 6 datasets now listed

To verify the cleanup sweep is conservative (it won't touch anything
unexpected), click "Show run IDs" on the maintenance page before hitting
the delete button. Cross-reference with /runs to confirm only rejected
iterations are listed.
MSG
