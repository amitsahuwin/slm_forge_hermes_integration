/**
 * Hermes / Ollama request-response trace inspector.
 *
 * Admin-only page. Shows every Ollama call's request body + response body
 * side-by-side. Useful for debugging prompt regressions ("why is this
 * skill suddenly returning garbage?") without spelunking raw logs.
 *
 * The OPA policy enforces admin-only access; we also gate the route via
 * <RequireRole role="admin"> in App.tsx as a UX shortcut.
 */
import { useEffect, useMemo, useState } from 'react';
import { API_URL } from '../lib/api';

type TraceRow = {
  id: number;
  created_at: string;
  source: string;
  model: string;
  duration_ms: number;
  error: string | null;
  request_body: string;
  response_body: string;
};

type SourceCount = { source: string; count: number };

function tryPretty(raw: string): string {
  if (!raw) return '';
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

export default function Traces() {
  const [rows, setRows] = useState<TraceRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceCount[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string>('');
  const [limit, setLimit] = useState<number>(50);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [clearing, setClearing] = useState(false);

  const load = async () => {
    try {
      const params = new URLSearchParams();
      params.set('limit', String(limit));
      if (sourceFilter) params.set('source_like', sourceFilter);
      const r = await fetch(`${API_URL}/api/v1/hermes/traces?${params}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as TraceRow[];
      setRows(data);
      if (selectedId == null && data[0]) setSelectedId(data[0].id);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const loadSources = async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/traces/sources/list`);
      if (!r.ok) return;
      const data = (await r.json()) as { sources: SourceCount[] };
      setSources(data.sources ?? []);
    } catch {
      /* non-fatal */
    }
  };

  useEffect(() => {
    void load();
    void loadSources();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limit, sourceFilter]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, limit, sourceFilter]);

  const selected = useMemo(
    () => rows?.find((r) => r.id === selectedId) ?? null,
    [rows, selectedId],
  );

  async function clearAll() {
    if (!confirm('Delete every trace row? This is admin-only and irreversible.')) return;
    setClearing(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/traces`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows([]);
      setSelectedId(null);
      void loadSources();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Hermes &middot; Ollama Traces
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Every request / response between SLM-Forge and Ollama, raw JSON.
            Admin only — bodies can contain prompts, dataset rows, and model
            metadata.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1.5 text-zinc-400">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="h-3.5 w-3.5 accent-emerald-500"
            />
            Auto-refresh
          </label>
          <button
            onClick={clearAll}
            disabled={clearing}
            className="rounded-md border border-rose-900/60 bg-rose-950/40 px-2.5 py-1 text-rose-200 hover:bg-rose-900/40 disabled:opacity-50"
          >
            {clearing ? 'Clearing…' : 'Clear all'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 text-xs">
        <label className="flex items-center gap-2 text-zinc-400">
          Source
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          >
            <option value="">all ({sources.reduce((s, x) => s + x.count, 0)})</option>
            {sources.map((s) => (
              <option key={s.source} value={s.source}>
                {s.source} ({s.count})
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Limit
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          >
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={250}>250</option>
            <option value={500}>500</option>
          </select>
        </label>
        {rows && (
          <span className="text-zinc-500">{rows.length} row{rows.length === 1 ? '' : 's'}</span>
        )}
      </div>

      <div className="grid grid-cols-[18rem_1fr] gap-3" style={{ minHeight: '70vh' }}>
        {/* Left: list */}
        <aside className="overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/40">
          {rows === null ? (
            <div className="p-4 text-sm text-zinc-500">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="p-4 text-sm text-zinc-500">
              No traces yet. Trigger a Hermes call from chat, agents, or any
              experiment to populate.
            </div>
          ) : (
            <ul>
              {rows.map((r) => (
                <li key={r.id}>
                  <button
                    onClick={() => setSelectedId(r.id)}
                    className={`block w-full border-b border-zinc-800/60 px-3 py-2 text-left transition-colors ${
                      selectedId === r.id
                        ? 'bg-emerald-950/30 text-emerald-200'
                        : 'text-zinc-300 hover:bg-zinc-800/40'
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2 font-mono text-[11px]">
                      <span className="truncate">{r.source}</span>
                      <span
                        className={
                          r.error
                            ? 'text-rose-400'
                            : r.duration_ms > 5000
                            ? 'text-amber-300'
                            : 'text-zinc-500'
                        }
                      >
                        {r.duration_ms}ms
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[10px] text-zinc-500">
                      <span>#{r.id}</span>
                      <span>{relativeTime(r.created_at)}</span>
                    </div>
                    {r.error && (
                      <div className="mt-1 truncate text-[11px] text-rose-400">
                        {r.error}
                      </div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* Right: detail */}
        <main className="overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
          {selected ? (
            <div className="space-y-4">
              <div className="grid grid-cols-4 gap-2 text-xs">
                <Stat label="source" value={selected.source} />
                <Stat label="model" value={selected.model} />
                <Stat label="duration" value={`${selected.duration_ms} ms`} />
                <Stat label="created" value={relativeTime(selected.created_at)} />
              </div>
              {selected.error && (
                <div className="rounded-md bg-rose-950/30 px-3 py-2 font-mono text-xs text-rose-300">
                  <div className="mb-1 font-medium text-rose-200">Error</div>
                  {selected.error}
                </div>
              )}
              <BodyBlock title="Request body" body={selected.request_body} />
              <BodyBlock title="Response body" body={selected.response_body} />
            </div>
          ) : (
            <div className="text-sm text-zinc-500">
              Pick a trace on the left.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-1.5">
      <div className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className="truncate font-mono text-xs text-zinc-100">{value}</div>
    </div>
  );
}

function BodyBlock({ title, body }: { title: string; body: string }) {
  const pretty = useMemo(() => tryPretty(body), [body]);
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-baseline justify-between border-b border-zinc-800 px-3 py-2 text-left text-xs text-zinc-300 hover:bg-zinc-900/60"
      >
        <span className="font-medium">{title}</span>
        <span className="font-mono text-zinc-500">
          {open ? '−' : '+'} {body.length.toLocaleString()} chars
        </span>
      </button>
      {open && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-300">
          {pretty || '(empty)'}
        </pre>
      )}
    </div>
  );
}
