import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { API_URL, authFetch } from '../lib/api';

// Phase C.5 — federated Jobs lookup. Accepts composite ids of the form
// `<kind>:<id>` where kind ∈ {run, session, export, autofix, agent,
// synth, research}. The backend (apps/api/routers/jobs.py) returns a
// uniform shape so this page doesn't have to switch on kind.

type JobDetail = {
  job_id: string;
  kind:
    | 'run'
    | 'session'
    | 'export'
    | 'autofix'
    | 'agent'
    | 'synth'
    | 'research';
  status: string;
  parent_id: string | null;
  tenant_id: string | null;
  user_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  summary: string | null;
  progress: Record<string, unknown> | null;
  links: Record<string, string>;
};

const KIND_HINTS: Record<JobDetail['kind'], string> = {
  run: 'Training run',
  session: 'Autoresearch experiment',
  export: 'GGUF export pipeline',
  autofix: 'Auto-fix attempt',
  agent: 'Hermes agent run',
  synth: 'Dataset synthesis',
  research: 'Market research',
};

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-zinc-800 text-zinc-300 border-zinc-700',
  running: 'bg-amber-950/40 text-amber-200 border-amber-800',
  fusing: 'bg-amber-950/40 text-amber-200 border-amber-800',
  converting: 'bg-amber-950/40 text-amber-200 border-amber-800',
  quantizing: 'bg-amber-950/40 text-amber-200 border-amber-800',
  succeeded: 'bg-emerald-950/40 text-emerald-200 border-emerald-800',
  completed: 'bg-emerald-950/40 text-emerald-200 border-emerald-800',
  failed: 'bg-rose-950/40 text-rose-200 border-rose-800',
  cancelled: 'bg-zinc-800 text-zinc-400 border-zinc-700',
};

const EXAMPLES: { id: string; label: string }[] = [
  { id: 'run:42', label: 'Training run #42' },
  { id: 'session:7', label: 'Experiment session #7' },
  { id: 'export:9', label: 'GGUF export #9' },
  { id: 'agent:abc123def456', label: 'Agent run abc123…' },
  { id: 'synth:hex12345', label: 'Synth job hex12345' },
];

export default function Jobs() {
  const [params, setParams] = useSearchParams();
  const initial = params.get('id') ?? '';
  const [input, setInput] = useState(initial);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const lookup = useCallback(async (jobId: string) => {
    if (!jobId.trim()) {
      setDetail(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    setDetail(null);
    try {
      const r = await authFetch(
        `${API_URL}/api/v1/jobs/${encodeURIComponent(jobId.trim())}`,
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        const d = j?.detail;
        const msg =
          typeof d === 'string' ? d : d?.message ?? `HTTP ${r.status}`;
        throw new Error(msg);
      }
      const data = (await r.json()) as JobDetail;
      setDetail(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-run on first mount when ?id=... is in the URL.
  useEffect(() => {
    if (initial) {
      void lookup(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    setParams(trimmed ? { id: trimmed } : {}, { replace: true });
    void lookup(trimmed);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-100">Jobs</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Look up any long-running operation by its composite id. Format:
          <code className="ml-1 rounded bg-zinc-900 px-1 py-0.5 font-mono text-xs text-zinc-300">
            kind:id
          </code>
          — kinds:{' '}
          {Object.keys(KIND_HINTS).map((k, i) => (
            <span key={k}>
              {i > 0 && ', '}
              <code className="rounded bg-zinc-900 px-1 py-0.5 font-mono text-xs text-emerald-300">
                {k}
              </code>
            </span>
          ))}
          .
        </p>
      </header>

      <form onSubmit={submit} className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="run:42 · agent:abc123 · synth:def456"
          className="flex-1 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-700 focus:outline-none"
          autoFocus
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="rounded-md border border-emerald-800 bg-emerald-950/40 px-4 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-900/40 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? 'Looking up…' : 'Look up'}
        </button>
      </form>

      {!detail && !error && !loading && (
        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
          <div className="mb-2 text-xs uppercase tracking-wide text-zinc-500">
            Examples
          </div>
          <ul className="space-y-1">
            {EXAMPLES.map((ex) => (
              <li key={ex.id}>
                <button
                  onClick={() => {
                    setInput(ex.id);
                    setParams({ id: ex.id }, { replace: true });
                    void lookup(ex.id);
                  }}
                  className="font-mono text-sm text-emerald-300 hover:text-emerald-200"
                >
                  {ex.id}
                </button>
                <span className="ml-2 text-xs text-zinc-500">{ex.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && (
        <section className="rounded-lg border border-rose-800 bg-rose-950/30 p-4 text-sm text-rose-200">
          <div className="font-medium">Lookup failed</div>
          <div className="mt-1 font-mono text-xs">{error}</div>
        </section>
      )}

      {detail && (
        <section className="space-y-4 rounded-lg border border-zinc-800 bg-zinc-950 p-5">
          <header className="flex flex-wrap items-center gap-3">
            <code className="rounded bg-zinc-900 px-2 py-1 font-mono text-sm text-zinc-200">
              {detail.job_id}
            </code>
            <span
              className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
                STATUS_STYLES[detail.status] ??
                'bg-zinc-800 text-zinc-300 border-zinc-700'
              }`}
            >
              {detail.status}
            </span>
            <span className="text-xs text-zinc-500">
              {KIND_HINTS[detail.kind]}
            </span>
          </header>

          {detail.summary && (
            <div className="text-sm text-zinc-300">{detail.summary}</div>
          )}

          <dl className="grid grid-cols-2 gap-3 text-xs">
            {detail.tenant_id && (
              <Row label="Tenant" value={detail.tenant_id} />
            )}
            {detail.user_id && <Row label="User" value={detail.user_id} />}
            {detail.started_at && (
              <Row label="Started" value={fmt(detail.started_at)} />
            )}
            {detail.completed_at && (
              <Row label="Completed" value={fmt(detail.completed_at)} />
            )}
          </dl>

          {detail.progress && Object.keys(detail.progress).length > 0 && (
            <details
              className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-xs"
              open
            >
              <summary className="cursor-pointer font-medium text-zinc-300">
                Progress
              </summary>
              <pre className="mt-2 whitespace-pre-wrap text-zinc-400">
                {JSON.stringify(detail.progress, null, 2)}
              </pre>
            </details>
          )}

          {detail.error && (
            <div className="rounded-md border border-rose-900 bg-rose-950/30 px-3 py-2 font-mono text-xs text-rose-200">
              {detail.error}
            </div>
          )}

          {Object.keys(detail.links).length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-zinc-800 pt-3">
              {Object.entries(detail.links)
                .filter(([, href]) => !!href)
                .map(([label, href]) => (
                  <a
                    key={label}
                    href={href}
                    className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-emerald-300 hover:bg-zinc-800"
                  >
                    {label} ↗
                  </a>
                ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="font-mono text-zinc-200">{value}</dd>
    </div>
  );
}

function fmt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}