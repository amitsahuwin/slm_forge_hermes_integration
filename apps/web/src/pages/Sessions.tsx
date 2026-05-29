import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type SessionStatus, type TrainingSession, api } from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function Sessions() {
  const [sessions, setSessions] = useState<TrainingSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .listSessions()
        .then((ss) => alive && setSessions(ss))
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
          <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Autoresearch sessions — Hermes-driven hyperparameter sweeps.
          </p>
        </div>
        <Link
          to="/sessions/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Session
        </Link>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {sessions === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : sessions.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No sessions yet.{' '}
          <Link to="/sessions/new" className="text-emerald-400 hover:underline">
            Start your first autoresearch session →
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
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {sessions.map((s) => (
                <tr key={s.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/sessions/${s.id}`} className="text-emerald-400 hover:underline">
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
