import { Link } from 'react-router-dom';
import type { Run } from '../../lib/api';

export default function IterationTable({ iterations }: { iterations: Run[] }) {
  const sorted = [...iterations].sort(
    (a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0),
  );

  return (
    <div className="overflow-hidden rounded-lg border border-hcl-light-blue">
      <table className="w-full text-sm">
        <thead className="bg-hcl-dark-blue text-xs uppercase tracking-wider text-white">
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
        <tbody className="divide-y divide-hcl-light-blue">
          {sorted.map((r) => (
            <tr key={r.id} className="font-mono text-hcl-dark/80 hover:bg-hcl-tech-grey">
              <td className="px-3 py-2">{r.iteration_number}</td>
              <td className="px-3 py-2">
                <Link to={`/runs/${r.id}`} className="text-hcl-teal hover:underline">
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
                {r.was_accepted === true && <span className="text-hcl-teal">● accepted</span>}
                {r.was_accepted === false && <span className="text-red-600">✗ rejected</span>}
                {r.was_accepted === null && <span className="text-hcl-dark/40">…</span>}
              </td>
              <td className="px-3 py-2 text-xs text-hcl-dark/60">{r.mutation_reasoning ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
