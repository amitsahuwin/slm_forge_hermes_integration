import { useEffect, useState } from 'react';

type Capabilities = Record<string, boolean>;

type Health = {
  status: string;
  version: string;
  phase: string;
  python: string;
  capabilities: Capabilities;
};

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: Health) => setHealth(data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 px-8 py-6">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">SLM-Forge</h1>
            <p className="mt-1 text-sm text-zinc-500">
              Local-first SLM fine-tuning lab · Hermes-driven autoresearch
            </p>
          </div>
          <div className="font-mono text-xs text-zinc-600">v0.1.0</div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-8 py-12">
        <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
          <Card title="API Status">
            {error ? (
              <div className="font-mono text-sm text-rose-400">
                <div>error</div>
                <div className="mt-1 text-zinc-400">{error}</div>
              </div>
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

          <Card title="Hermes Agent">
            <p className="text-sm text-zinc-400">
              Configure via{' '}
              <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-zinc-200">
                make install-hermes
              </code>
              .
            </p>
            <p className="mt-2 text-xs text-zinc-600">
              Default: Ollama + qwen2.5-coder:14b (local, free)
            </p>
          </Card>

          <Card title="Trainer">
            <p className="text-sm text-zinc-400">
              MLX-LM trainer runs on host (Metal access). Comes online in Phase 1.
            </p>
          </Card>
        </section>

        {health && (
          <section className="mt-10">
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

        <section className="mt-12">
          <h2 className="mb-4 text-sm font-medium uppercase tracking-wider text-zinc-500">
            Roadmap
          </h2>
          <ol className="space-y-2.5">
            <Phase n="0" current label="Foundation: scaffold, Hermes/Ollama install, dev stack" />
            <Phase n="1" label="End-to-end LoRA on Gemma 4 E2B + live loss chart" />
            <Phase n="2" label="Autoresearch ratchet + 4-graph UI" />
            <Phase n="3" label="Data ingestion (local, URL, scrape, S3)" />
            <Phase n="4" label="Export pipeline (LoRA → GGUF → iPhone)" />
            <Phase n="5" label="Polish, 6 sample datasets, full docs" />
          </ol>
        </section>
      </main>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
        {title}
      </h3>
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

function Phase({
  n,
  label,
  current,
}: {
  n: string;
  label: string;
  current?: boolean;
}) {
  return (
    <li className="flex items-baseline gap-4 text-sm">
      <span
        className={`font-mono ${current ? 'text-emerald-400' : 'text-zinc-600'}`}
      >
        [{n}]
      </span>
      <span className={current ? 'text-zinc-100' : 'text-zinc-500'}>
        {label}
        {current && <span className="ml-2 text-xs text-emerald-500">← you are here</span>}
      </span>
    </li>
  );
}
