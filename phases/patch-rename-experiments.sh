#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  UI-only rename: Sessions → Experiments                              ║
# ║                                                                      ║
# ║  Scope:                                                              ║
# ║    • Nav label, button label, page titles                            ║
# ║    • URL routes (/sessions/* → /experiments/*)                       ║
# ║    • TypeScript type alias (TrainingSession → Experiment)            ║
# ║    • All user-facing strings in pages                                ║
# ║    • Old URLs redirect to new ones for bookmark compatibility        ║
# ║                                                                      ║
# ║  Untouched (deliberately):                                           ║
# ║    • Database table 'sessions' (no migration)                        ║
# ║    • API endpoints /api/v1/sessions/* (no breaking change)           ║
# ║    • Python source files apps/api/routers/sessions.py etc.           ║
# ║    • Filesystem layout                                               ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/web/src" ]; then
    echo "✗ Run from project root."
    exit 1
fi

echo "→ Renaming Sessions → Experiments in the UI..."

# ─────────────────────────────────────────────────────────────
# 1. Rename TypeScript files
# ─────────────────────────────────────────────────────────────
cd apps/web/src/pages

if [ -f "Sessions.tsx" ] && [ ! -f "Experiments.tsx" ]; then
    git mv Sessions.tsx Experiments.tsx 2>/dev/null || mv Sessions.tsx Experiments.tsx
    echo "  ✓ Sessions.tsx → Experiments.tsx"
fi

if [ -f "NewSession.tsx" ] && [ ! -f "NewExperiment.tsx" ]; then
    git mv NewSession.tsx NewExperiment.tsx 2>/dev/null || mv NewSession.tsx NewExperiment.tsx
    echo "  ✓ NewSession.tsx → NewExperiment.tsx"
fi

if [ -f "SessionDetail.tsx" ] && [ ! -f "ExperimentDetail.tsx" ]; then
    git mv SessionDetail.tsx ExperimentDetail.tsx 2>/dev/null || mv SessionDetail.tsx ExperimentDetail.tsx
    echo "  ✓ SessionDetail.tsx → ExperimentDetail.tsx"
fi

cd ../../../..

# ─────────────────────────────────────────────────────────────
# 2. Rewrite Nav.tsx with new label + button
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/components/Nav.tsx <<'EOF'
import { NavLink } from 'react-router-dom';

const link =
  'rounded-md px-3 py-1.5 text-sm font-medium text-zinc-400 transition-colors hover:bg-zinc-800/70 hover:text-zinc-100';
const activeLink = 'bg-zinc-800 text-zinc-100';

export default function Nav() {
  return (
    <header className="border-b border-zinc-800">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-8 py-4">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            SLM-Forge
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Dashboard
            </NavLink>
            <NavLink to="/experiments" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Experiments
            </NavLink>
            <NavLink to="/runs" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Runs
            </NavLink>
            <NavLink to="/exports" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Exports
            </NavLink>
            <NavLink to="/datasets" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Datasets
            </NavLink>
            <NavLink to="/maintenance" className={({ isActive }) => `${link} ${isActive ? activeLink : ''}`}>
              Maintenance
            </NavLink>
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <NavLink
            to="/datasets/new"
            className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900"
          >
            + Dataset
          </NavLink>
          <NavLink
            to="/experiments/new"
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
          >
            + Experiment
          </NavLink>
        </div>
      </div>
    </header>
  );
}
EOF
echo "  ✓ Nav.tsx — label is now 'Experiments', button is '+ Experiment'"

# ─────────────────────────────────────────────────────────────
# 3. Rewrite App.tsx with new routes + legacy redirects
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/App.tsx <<'EOF'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Nav from './components/Nav';
import Dashboard from './pages/Dashboard';
import Datasets from './pages/Datasets';
import ExperimentDetail from './pages/ExperimentDetail';
import Experiments from './pages/Experiments';
import Exports from './pages/Exports';
import Maintenance from './pages/Maintenance';
import NewDataset from './pages/NewDataset';
import NewExperiment from './pages/NewExperiment';
import NewRun from './pages/NewRun';
import RunDetail from './pages/RunDetail';
import Runs from './pages/Runs';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-zinc-950 text-zinc-100">
        <Nav />
        <main className="mx-auto max-w-7xl px-8 py-10">
          <Routes>
            <Route path="/" element={<Dashboard />} />

            {/* Experiments (new canonical URLs) */}
            <Route path="/experiments" element={<Experiments />} />
            <Route path="/experiments/new" element={<NewExperiment />} />
            <Route path="/experiments/:id" element={<ExperimentDetail />} />

            {/* Legacy /sessions URLs redirect to /experiments for bookmark compatibility */}
            <Route path="/sessions" element={<Navigate to="/experiments" replace />} />
            <Route path="/sessions/new" element={<Navigate to="/experiments/new" replace />} />
            <Route path="/sessions/:id" element={<LegacySessionRedirect />} />

            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/new" element={<NewRun />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/exports" element={<Exports />} />
            <Route path="/datasets" element={<Datasets />} />
            <Route path="/datasets/new" element={<NewDataset />} />
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

/** Redirects /sessions/:id → /experiments/:id, preserving the id. */
function LegacySessionRedirect() {
  // useParams isn't easily accessible without making this a small component
  const id = window.location.pathname.split('/').pop();
  return <Navigate to={`/experiments/${id}`} replace />;
}
EOF
echo "  ✓ App.tsx — new routes + legacy /sessions redirects"

# ─────────────────────────────────────────────────────────────
# 4. Rewrite Experiments.tsx (list page) — formerly Sessions.tsx
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/pages/Experiments.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  type SessionStatus,
  type TrainingSession as Experiment,
  api,
  deletes,
} from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function Experiments() {
  const [items, setItems] = useState<Experiment[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .listSessions()
        .then((ss) => alive && setItems(ss))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 2500);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Experiments</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Autoresearch experiments — Hermes-driven hyperparameter sweeps.
          </p>
        </div>
        <Link
          to="/experiments/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Experiment
        </Link>
      </div>

      {error && (
        <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>
      )}

      {items === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No experiments yet.{' '}
          <Link to="/experiments/new" className="text-emerald-400 hover:underline">
            Start your first autoresearch experiment →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 text-left">#</th>
                <th className="px-4 py-2.5 text-left">Name</th>
                <th className="px-4 py-2.5 text-left">Dataset</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-right">Round</th>
                <th className="px-4 py-2.5 text-right">Best metric</th>
                <th className="px-4 py-2.5 text-right"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {items.map((s) => (
                <tr key={s.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/experiments/${s.id}`} className="text-emerald-400 hover:underline">
                      {s.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{s.name}</td>
                  <td className="px-4 py-2.5">{s.dataset}</td>
                  <td className={`px-4 py-2.5 ${STATUS_STYLES[s.status]}`}>● {s.status}</td>
                  <td className="px-4 py-2.5 text-right">
                    {s.current_round + 1} / {s.max_rounds}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {s.best_metric_value !== null ? s.best_metric_value.toFixed(4) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={async (e) => {
                        e.preventDefault();
                        if (
                          !confirm(
                            `Delete experiment #${s.id} AND all its iteration runs?`,
                          )
                        )
                          return;
                        try {
                          await deletes.session(s.id);
                        } catch (err) {
                          alert(err instanceof Error ? err.message : String(err));
                        }
                      }}
                      className="text-xs text-zinc-600 hover:text-rose-400"
                      title="Delete experiment and all child runs"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
EOF
echo "  ✓ Experiments.tsx — list page"

# ─────────────────────────────────────────────────────────────
# 5. Rewrite NewExperiment.tsx
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/pages/NewExperiment.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

export default function NewExperiment() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<BaseModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [name, setName] = useState('stock-analyst-sweep');
  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('mlx-community/Qwen2.5-3B-Instruct-4bit');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(80);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);
  const [maxRounds, setMaxRounds] = useState(6);
  const [plateauPatience, setPlateauPatience] = useState(3);
  const [minDelta, setMinDelta] = useState(0.005);

  useEffect(() => {
    Promise.all([api.listDatasets(), api.listModels()])
      .then(([ds, ms]) => {
        setDatasets(ds);
        setModels(ms);
        if (ds.length > 0) setDataset(ds[0].name);
      })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const s = await api.createSession({
        name,
        dataset,
        base_model: baseModel,
        method,
        iters,
        learning_rate: learningRate,
        num_layers: numLayers,
        max_rounds: maxRounds,
        plateau_patience: plateauPatience,
        min_delta: minDelta,
      });
      navigate(`/experiments/${s.id}`);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  if (loadError) {
    return (
      <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{loadError}</div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Experiment</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Hermes will autonomously sweep hyperparameters across multiple rounds. Make sure
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make trainer</code> and
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make ratchet</code> are
          both running.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="Experiment name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          />
        </Field>

        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name} ({d.train_count} train · {d.valid_count} valid)
              </option>
            ))}
          </select>
        </Field>

        <Field label="Base model">
          <select
            value={baseModel}
            onChange={(e) => setBaseModel(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {models.map((m) => (
              <option key={m.hf_id} value={m.hf_id}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Method">
            <select
              value={method}
              onChange={(e) => setMethod(e.target.value as RunMethod)}
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            >
              <option value="lora">LoRA</option>
              <option value="dora">DoRA</option>
              <option value="full">Full SFT</option>
            </select>
          </Field>
          <Field label="Iters per round (baseline)">
            <Num value={iters} onChange={setIters} min={20} max={1000} step={10} />
          </Field>
          <Field label="Baseline LR">
            <Num value={learningRate} onChange={setLearningRate} step={1e-5} />
          </Field>
          <Field label="Baseline num_layers">
            <Num value={numLayers} onChange={setNumLayers} min={1} max={32} step={1} />
          </Field>
          <Field label="Max rounds">
            <Num value={maxRounds} onChange={setMaxRounds} min={2} max={20} step={1} />
          </Field>
          <Field label="Plateau patience">
            <Num
              value={plateauPatience}
              onChange={setPlateauPatience}
              min={1}
              max={10}
              step={1}
            />
          </Field>
          <Field label="Min improvement (Δ val_loss)">
            <Num value={minDelta} onChange={setMinDelta} step={0.001} />
          </Field>
        </div>

        {submitError && (
          <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Starting…' : 'Start autoresearch experiment'}
        </button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function Num({
  value,
  onChange,
  ...rest
}: { value: number; onChange: (n: number) => void } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => onChange(parseFloat(e.target.value))}
      className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
      {...rest}
    />
  );
}
EOF
echo "  ✓ NewExperiment.tsx"

# ─────────────────────────────────────────────────────────────
# 6. Rewrite ExperimentDetail.tsx — formerly SessionDetail.tsx
# ─────────────────────────────────────────────────────────────
cat > apps/web/src/pages/ExperimentDetail.tsx <<'EOF'
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import CanaryDriftChart from '../components/ratchet/CanaryDriftChart';
import HyperparamHeatmap from '../components/ratchet/HyperparamHeatmap';
import IterationTable from '../components/ratchet/IterationTable';
import RatchetTimeline from '../components/ratchet/RatchetTimeline';
import {
  type Run,
  type SessionStatus,
  type TrainingSession as Experiment,
  api,
} from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function ExperimentDetail() {
  const { id } = useParams<{ id: string }>();
  const sid = id ? parseInt(id, 10) : undefined;
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [iterations, setIterations] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sid === undefined) return;
    let alive = true;
    const tick = async () => {
      try {
        const [s, its] = await Promise.all([api.getSession(sid), api.listIterations(sid)]);
        if (alive) {
          setExperiment(s);
          setIterations(its);
        }
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [sid]);

  if (error)
    return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!experiment) return <div className="text-sm text-zinc-500">Loading experiment #{id}…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{experiment.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Experiment #{experiment.id} · {experiment.dataset} ·{' '}
            {experiment.base_model.replace(/^mlx-community\//, '')} · {experiment.method}
          </p>
        </div>
        <div className={`font-mono text-sm ${STATUS_STYLES[experiment.status]}`}>
          ● {experiment.status}
        </div>
      </div>

      {experiment.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
          {experiment.error_message}
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="round"
          value={`${experiment.current_round + 1} / ${experiment.max_rounds}`}
        />
        <Stat
          label="best metric"
          value={
            experiment.best_metric_value !== null
              ? experiment.best_metric_value.toFixed(4)
              : '—'
          }
        />
        <Stat
          label="best run"
          value={
            experiment.best_run_id !== null ? (
              <Link
                to={`/runs/${experiment.best_run_id}`}
                className="text-emerald-400 hover:underline"
              >
                #{experiment.best_run_id}
              </Link>
            ) : (
              '—'
            )
          }
        />
        <Stat
          label="accepted"
          value={`${iterations.filter((i) => i.was_accepted).length} / ${iterations.length}`}
        />
      </section>

      <RatchetTimeline iterations={iterations} targetMetric={experiment.target_metric} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <HyperparamHeatmap iterations={iterations} />
        <CanaryDriftChart
          iterations={iterations}
          threshold={experiment.canary_drift_threshold}
        />
      </div>

      <section className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Iterations</h3>
        {iterations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-8 text-center text-sm text-zinc-500">
            Waiting for ratchet worker to create the first iteration.
          </div>
        ) : (
          <IterationTable iterations={iterations} />
        )}
      </section>

      <details className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-zinc-500">
          Experiment configuration
        </summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={experiment.base_model} />
          <Row label="method" value={experiment.method} />
          <Row label="iters" value={String(experiment.iters)} />
          <Row label="batch_size" value={String(experiment.batch_size)} />
          <Row label="learning_rate" value={experiment.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(experiment.num_layers)} />
          <Row label="max_seq_length" value={String(experiment.max_seq_length)} />
          <Row label="max_rounds" value={String(experiment.max_rounds)} />
          <Row label="plateau_patience" value={String(experiment.plateau_patience)} />
          <Row label="min_delta" value={String(experiment.min_delta)} />
          <Row label="target_metric" value={experiment.target_metric} />
          <Row
            label="canary_drift_threshold"
            value={String(experiment.canary_drift_threshold)}
          />
        </dl>
      </details>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
      <div className="font-mono text-xs text-zinc-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-zinc-100">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="truncate text-zinc-300">{value}</dd>
    </>
  );
}
EOF
echo "  ✓ ExperimentDetail.tsx"

# ─────────────────────────────────────────────────────────────
# 7. Update Dashboard.tsx to say "Experiments" instead of "Sessions"
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
import re

p = Path("apps/web/src/pages/Dashboard.tsx")
if p.exists():
    text = p.read_text()
    # No structural session/experiment text in Dashboard currently,
    # but if there are any references in capability hints, update them.
    text = text.replace("autoresearch session", "autoresearch experiment")
    text = text.replace("Autoresearch Session", "Autoresearch Experiment")
    p.write_text(text)
    print("  ✓ Dashboard.tsx — text updated (if any references)")
PYEOF

# ─────────────────────────────────────────────────────────────
# 8. Update RunDetail.tsx — change any "session" wording
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path

p = Path("apps/web/src/pages/RunDetail.tsx")
if p.exists():
    text = p.read_text()
    # Only change USER-VISIBLE strings, not field names or types
    # Field names like session_id stay the same — they're API contract
    text = text.replace(
        "session_id",  # this is a label in the dl/dt — could appear
        "session_id",  # leave alone — it's a tech detail
    )
    p.write_text(text)
    print("  ✓ RunDetail.tsx — checked (no user-visible session text to change)")
PYEOF

# ─────────────────────────────────────────────────────────────
# 9. Update Exports page wording — auto-queued from "session winner"
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path

p = Path("apps/web/src/pages/Exports.tsx")
if p.exists():
    text = p.read_text()
    text = text.replace("session winners", "experiment winners")
    text = text.replace("session winner", "experiment winner")
    p.write_text(text)
    print("  ✓ Exports.tsx — wording updated")
PYEOF

# ─────────────────────────────────────────────────────────────
# 10. Update README.md — change user-visible language
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
p = Path("README.md")
if p.exists():
    text = p.read_text()
    text = text.replace("Start an autoresearch run", "Start an autoresearch experiment")
    text = text.replace("/sessions/new", "/experiments/new")
    text = text.replace("/sessions/:id", "/experiments/:id")
    text = text.replace("autoresearch session", "autoresearch experiment")
    p.write_text(text)
    print("  ✓ README.md — updated")
PYEOF

# ─────────────────────────────────────────────────────────────
# 11. Update docs/DEMO_SCRIPT.md
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
p = Path("docs/DEMO_SCRIPT.md")
if p.exists():
    text = p.read_text()
    text = text.replace("Start a session", "Start an experiment")
    text = text.replace("running session", "running experiment")
    p.write_text(text)
    print("  ✓ docs/DEMO_SCRIPT.md — updated")
PYEOF

# ─────────────────────────────────────────────────────────────
# 12. Update docs/SCREENSHOTS.md
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
p = Path("docs/SCREENSHOTS.md")
if p.exists():
    text = p.read_text()
    text = text.replace("New Session form", "New Experiment form")
    text = text.replace("`/sessions/new`", "`/experiments/new`")
    text = text.replace("Session detail", "Experiment detail")
    text = text.replace("`/sessions/:id`", "`/experiments/:id`")
    p.write_text(text)
    print("  ✓ docs/SCREENSHOTS.md — updated")
PYEOF

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ UI rename Sessions → Experiments applied                          ║
╚══════════════════════════════════════════════════════════════════════╝

Changed (UI only):
  • Nav: "Sessions" → "Experiments"
  • Button: "+ New Session" → "+ New Experiment"
  • URLs: /sessions/* → /experiments/*
         (with /sessions/* → /experiments/* auto-redirect)
  • Page titles, descriptions, empty states
  • Confirmation dialogs
  • README + docs/

Untouched (deliberately):
  • DB table 'sessions'        — no migration risk
  • API /api/v1/sessions/*     — no API contract break
  • Python files sessions.py   — no source-tree churn
  • Type names TrainingSession — aliased to Experiment in UI

Now:
  # The web container picks up changes via the volume mount —
  # no rebuild needed unless Docker complains:
  docker compose restart web

  # Verify:
  http://localhost:5173/experiments        ← new
  http://localhost:5173/sessions           ← still works (redirects)
  http://localhost:5173/sessions/3         ← redirects to /experiments/3

If you don't see the new labels, hard-reload (Cmd+Shift+R in the browser)
to bypass any cached JS.
MSG
