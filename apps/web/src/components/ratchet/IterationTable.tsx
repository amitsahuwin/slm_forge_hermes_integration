import { Link } from 'react-router-dom';
import type { Run } from '../../lib/api';

export default function IterationTable({ iterations }: { iterations: Run[] }) {
  const sorted = [...iterations].sort(
    (a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0),
  );

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/50 text-xs uppercase tracking-wider text-zinc-500">
          <tr>
            <th className="px-3 py-2 text-left">#</th>
            <th className="px-3 py-2 text-left">Run</th>
            <th className="px-3 py-2 text-left">Status</th>
            <th className="px-3 py-2 text-right">val_loss</th>
            <th className="px-3 py-2 text-right">lr</th>
            <th className="px-3 py-2 text-right">batch</th>
            <th className="px-3 py-2 text-right">layers</th>
            <th className="px-3 py-2 text-left">Decision</th>
            <th className="px-3 py-2 text-left">Reasoning</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {sorted.map((r) => (
            <tr key={r.id} className="font-mono text-zinc-300 hover:bg-zinc-900/30">
              <td className="px-3 py-2">{r.iteration_number}</td>
              <td className="px-3 py-2">
                <Link to={`/runs/${r.id}`} className="text-emerald-400 hover:underline">
                  #{r.id}
                </Link>
              </td>
              <td className="px-3 py-2 text-xs">{r.status}</td>
              <td className="px-3 py-2 text-right">
                {r.final_val_loss !== null ? r.final_val_loss.toFixed(4) : '—'}
              </td>
              <td className="px-3 py-2 text-right">{r.learning_rate.toExponential(1)}</td>
              <td className="px-3 py-2 text-right">{r.batch_size}</td>
              <td className="px-3 py-2 text-right">{r.num_layers}</td>
              <td className="px-3 py-2">
                {r.was_accepted === true && <span className="text-emerald-400">● accepted</span>}
                {r.was_accepted === false && <span className="text-rose-400">✗ rejected</span>}
                {r.was_accepted === null && <span className="text-zinc-600">…</span>}
              </td>
              <td className="px-3 py-2 text-xs text-zinc-400">{r.mutation_reasoning ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
