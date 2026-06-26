import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type Run, type RunStatus, api, deletes } from '../lib/api';

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-hcl-dark/60',
  running: 'text-hcl-teal',
  completed: 'text-hcl-info',
  failed: 'text-red-600',
  cancelled: 'text-hcl-dark/50',
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
          <p className="mt-1 text-sm text-hcl-dark/50">All fine-tuning jobs, newest first.</p>
        </div>
        <Link
          to="/runs/new"
          className="rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
        >
          + New Run
        </Link>
      </div>

      {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}

      {runs === null ? (
        <div className="text-sm text-hcl-dark/50">Loading…</div>
      ) : runs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-10 text-center text-sm text-hcl-dark/50">
          No runs yet.{' '}
          <Link to="/runs/new" className="text-hcl-teal hover:underline">
            Start your first run →
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-hcl-light-blue">
          <table className="w-full text-sm">
            <thead className="bg-hcl-dark-blue text-xs uppercase tracking-wider text-white">
              <tr>
                <th className="px-4 py-2.5 text-left">#</th>
                <th className="px-4 py-2.5 text-left">Dataset</th>
                <th className="px-4 py-2.5 text-left">Model</th>
                <th className="px-4 py-2.5 text-left">Method</th>
                <th className="px-4 py-2.5 text-right">Iters</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-right">Train loss</th>
                <th className="px-4 py-2.5 text-right">Val loss</th>
                <th className="px-4 py-2.5 text-right"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hcl-light-blue">
              {runs.map((r) => (
                <tr key={r.id} className="font-mono text-hcl-dark/80 even:bg-hcl-bg hover:bg-hcl-tech-grey/30">
                  <td className="px-4 py-2.5">
                    <Link to={`/runs/${r.id}`} className="text-hcl-teal hover:underline">
                      {r.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{r.dataset}</td>
                  <td className="px-4 py-2.5 text-xs text-hcl-dark/50">
                    {r.base_model.replace(/^mlx-community\//, '')}
                    <span
                      className={`ml-1.5 rounded px-1 py-0.5 text-[10px] font-medium uppercase ${
                        r.trainer_backend === 'cuda'
                          ? 'bg-violet-950/60 text-violet-400'
                          : 'bg-hcl-tech-grey text-hcl-dark/60'
                      }`}
                    >
                      {r.trainer_backend ?? 'mlx'}
                    </span>
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
                      className="text-xs text-hcl-dark/40 hover:text-red-500"
                      title="Delete run"
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
