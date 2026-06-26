import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  type SessionStatus,
  type TrainingSession as Experiment,
  api,
  deletes,
} from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-hcl-dark/60',
  running: 'text-hcl-teal',
  completed: 'text-hcl-info',
  failed: 'text-red-600',
  cancelled: 'text-hcl-dark/50',
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
          <p className="mt-1 text-sm text-hcl-dark/50">
            Autoresearch experiments — Hermes-driven hyperparameter sweeps.
          </p>
        </div>
        <Link
          to="/experiments/new"
          className="rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
        >
          + New Experiment
        </Link>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>
      )}

      {items === null ? (
        <div className="text-sm text-hcl-dark/50">Loading…</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-10 text-center text-sm text-hcl-dark/50">
          No experiments yet.{' '}
          <Link to="/experiments/new" className="text-hcl-teal hover:underline">
            Start your first autoresearch experiment →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-hcl-light-blue">
          <table className="w-full text-sm">
            <thead className="bg-hcl-dark-blue text-xs uppercase tracking-wider text-white">
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
            <tbody className="divide-y divide-hcl-light-blue">
              {items.map((s) => (
                <tr key={s.id} className="font-mono text-hcl-dark/80 even:bg-hcl-bg hover:bg-hcl-tech-grey/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/experiments/${s.id}`} className="text-hcl-teal hover:underline">
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
                      className="text-xs text-hcl-dark/40 hover:text-red-500"
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
