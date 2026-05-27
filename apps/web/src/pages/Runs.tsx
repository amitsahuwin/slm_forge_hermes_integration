import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type Run, type RunStatus, api } from '../lib/api';

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function Runs() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () => {
      api
        .listRuns()
        .then((rs) => alive && setRuns(rs))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
          <p className="mt-1 text-sm text-zinc-500">All fine-tuning jobs, newest first.</p>
        </div>
        <Link
          to="/runs/new"
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New Run
        </Link>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {runs === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : runs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No runs yet.{' '}
          <Link to="/runs/new" className="text-emerald-400 hover:underline">
            Start your first run →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-2.5 text-left">#</th>
                <th className="px-4 py-2.5 text-left">Dataset</th>
                <th className="px-4 py-2.5 text-left">Model</th>
                <th className="px-4 py-2.5 text-left">Method</th>
                <th className="px-4 py-2.5 text-right">Iters</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-right">Train loss</th>
                <th className="px-4 py-2.5 text-right">Val loss</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {runs.map((r) => (
                <tr key={r.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/runs/${r.id}`} className="text-emerald-400 hover:underline">
                      {r.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{r.dataset}</td>
                  <td className="px-4 py-2.5 text-xs text-zinc-500">
                    {r.base_model.replace(/^mlx-community\//, '')}
                  </td>
                  <td className="px-4 py-2.5">{r.method}</td>
                  <td className="px-4 py-2.5 text-right">{r.iters}</td>
                  <td className={`px-4 py-2.5 ${STATUS_STYLES[r.status]}`}>● {r.status}</td>
                  <td className="px-4 py-2.5 text-right">
                    {r.final_train_loss !== null ? r.final_train_loss.toFixed(3) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {r.final_val_loss !== null ? r.final_val_loss.toFixed(3) : '—'}
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
