import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import SynthesizeButton from '../components/SynthesizeButton';
import { type DatasetInfo, api } from '../lib/api';

export default function Datasets() {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .listDatasets()
        .then((d) => alive && setDatasets(d))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
          <p className="mt-1 text-sm text-hcl-dark/50">
            Available training datasets under <code className="text-hcl-dark/60">data/datasets/</code>.
          </p>
        </div>
        <Link
          to="/datasets/new"
          className="rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
        >
          + New Dataset
        </Link>
      </div>

      {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}

      {datasets === null ? (
        <div className="text-sm text-hcl-dark/50">Loading…</div>
      ) : datasets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-10 text-center text-sm text-hcl-dark/50">
          No datasets yet.{' '}
          <Link to="/datasets/new" className="text-hcl-teal hover:underline">
            Ingest your first dataset →
          </Link>
        </div>
      ) : (
        <ul className="space-y-3">
          {datasets.map((d) => (
            <li key={d.name}>
              <Link
                to={`/datasets/${encodeURIComponent(d.name)}`}
                className="block rounded-lg border border-hcl-light-blue bg-white p-4 transition-colors hover:border-hcl-teal/30 hover:bg-hcl-tech-grey/60"
              >
                <div className="flex items-baseline justify-between gap-4">
                  <h3 className="font-mono text-sm font-semibold text-hcl-dark">{d.name}</h3>
                  <div className="flex items-center gap-3">
                    <div className="font-mono text-xs text-hcl-dark/50">
                      {d.train_count} train · {d.valid_count} valid
                      {d.has_canary && ' · canary ✓'}
                    </div>
                    <SynthesizeButton
                      dataset={d.name}
                      count={d.train_count + d.valid_count}
                    />
                  </div>
                </div>
                {d.description && (
                  <p className="mt-1.5 text-sm text-hcl-dark/60">{d.description}</p>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
