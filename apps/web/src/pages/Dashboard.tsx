import { useEffect, useState } from 'react';
import { API_URL } from '../lib/api';

type Health = {
  status: string;
  version: string;
  phase: string;
  python: string;
  capabilities: Record<string, boolean>;
};

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: Health) => setHealth(d))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Local-first SLM fine-tuning lab · Hermes-driven autoresearch
        </p>
      </div>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card title="API Status">
          {error ? (
            <div className="font-mono text-sm text-rose-400">{error}</div>
          ) : health ? (
            <dl className="space-y-1.5 font-mono text-sm">
              <Row label="status" value={health.status} ok />
              <Row label="version" value={health.version} />
              <Row label="phase" value={health.phase} />
              <Row label="python" value={health.python} />
            </dl>
          ) : (
            <span className="text-sm text-zinc-500">Connecting…</span>
          )}
        </Card>

        <Card title="Trainer">
          <p className="text-sm text-zinc-400">
            Host worker required. Start it in a separate terminal:
          </p>
          <code className="mt-2 block rounded bg-zinc-800 px-2 py-1.5 font-mono text-xs text-zinc-200">
            make trainer
          </code>
        </Card>

        <Card title="Hermes Agent">
          <p className="text-sm text-zinc-400">Coming online in Phase 2 (autoresearch).</p>
        </Card>
      </section>

      {health && (
        <section>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
            Capabilities
          </h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {Object.entries(health.capabilities).map(([key, enabled]) => (
              <div
                key={key}
                className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
              >
                <div className="font-mono text-xs text-zinc-500">{key}</div>
                <div
                  className={`mt-1 font-mono text-sm ${
                    enabled ? 'text-emerald-400' : 'text-zinc-600'
                  }`}
                >
                  {enabled ? '● enabled' : '○ pending'}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="w-16 text-zinc-500">{label}</dt>
      <dd className={ok ? 'text-emerald-400' : 'text-zinc-200'}>{value}</dd>
    </div>
  );
}
