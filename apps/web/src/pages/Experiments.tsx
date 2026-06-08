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
