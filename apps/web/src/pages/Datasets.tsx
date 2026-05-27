import { useEffect, useState } from 'react';
import { type DatasetInfo, api } from '../lib/api';

export default function Datasets() {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listDatasets()
      .then(setDatasets)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Available training datasets under <code className="text-zinc-400">data/datasets/</code>.
        </p>
      </div>

      {error && <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>}

      {datasets === null ? (
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : datasets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
          No datasets yet. Run{' '}
          <code className="rounded bg-zinc-800 px-1.5 py-0.5">make seed-data</code> to seed sample data.
        </div>
      ) : (
        <ul className="space-y-3">
          {datasets.map((d) => (
            <li key={d.name} className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
              <div className="flex items-baseline justify-between gap-4">
                <h3 className="font-mono text-sm font-semibold text-zinc-100">{d.name}</h3>
                <div className="font-mono text-xs text-zinc-500">
                  {d.train_count} train · {d.valid_count} valid
                  {d.has_canary && ' · canary ✓'}
                </div>
              </div>
              {d.description && <p className="mt-1.5 text-sm text-zinc-400">{d.description}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
