/**
 * Hermes / Ollama request-response trace inspector.
 *
 * Admin-only page. Three regions:
 *   1. Filter bar — skill multi-select, success/error, time range,
 *      min-duration, run/session, limit, auto-refresh.
 *   2. Left "Skill Activity" panel — per-skill rollup from
 *      /api/v1/hermes/traces/skills/summary. Click a row to filter.
 *   3. Middle list + right detail — same shape as before, with new
 *      "✎ skill changed" badge and run/session chips.
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
  attempts?: number;
  tenant_id?: string;
  skill_name: string | null;
  skill_sha256: string | null;
  skill_mtime: string | null;
  run_id: number | null;
  session_id: number | null;
  success: boolean;
  skill_changed: boolean;
  // Phase B — trace nesting (NULL on legacy rows).
  kind?: 'agent' | 'skill' | 'tool' | null;
  trace_id?: string | null;
  parent_span_id?: string | null;
  span_id?: string | null;
  agent_run_id?: string | null;
  // Only present when ?group_by=trace; children share kind=skill/tool.
  children?: TraceRow[];
};

type GroupBy = 'flat' | 'tree';

type SkillSummaryRow = {
  skill_name: string;
  calls: number;
  errors: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
  first_seen: string;
  last_seen: string;
  current_sha256: string | null;
  change_count: number;
};

type SourceCount = { source: string; count: number };

type StatusFilter = '' | 'success' | 'error';
type TimeRange = 'all' | '1h' | '24h' | '7d';

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

function rangeToSince(range: TimeRange): string | null {
  if (range === 'all') return null;
  const now = new Date();
  const ms = range === '1h' ? 3600_000 : range === '24h' ? 86_400_000 : 7 * 86_400_000;
  return new Date(now.getTime() - ms).toISOString();
}

export default function Traces() {
  const [rows, setRows] = useState<TraceRow[] | null>(null);
  const [summary, setSummary] = useState<SkillSummaryRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceCount[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string>('');

  const [skillFilter, setSkillFilter] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  const [timeRange, setTimeRange] = useState<TimeRange>('all');
  const [minDuration, setMinDuration] = useState<string>('');
  const [runIdFilter, setRunIdFilter] = useState<string>('');
  const [sessionIdFilter, setSessionIdFilter] = useState<string>('');
  const [limit, setLimit] = useState<number>(50);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [clearing, setClearing] = useState(false);

  // Phase B — Tree view collapses spans sharing a trace_id under their
  // agent parent. Default to 'tree' so agent runs are immediately
  // visible; Flat preserves the historical row-per-span shape for
  // anyone who liked the old layout.
  const [groupBy, setGroupBy] = useState<GroupBy>('tree');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const buildParams = (): URLSearchParams => {
    const p = new URLSearchParams();
    p.set('limit', String(limit));
    if (sourceFilter) p.set('source_like', sourceFilter);
    for (const s of skillFilter) p.append('skill', s);
    if (statusFilter) p.set('status', statusFilter);
    const since = rangeToSince(timeRange);
    if (since) p.set('since', since);
    if (minDuration && Number(minDuration) > 0) p.set('min_duration_ms', minDuration);
    if (runIdFilter && Number(runIdFilter) > 0) p.set('run_id', runIdFilter);
    if (sessionIdFilter && Number(sessionIdFilter) > 0) p.set('session_id', sessionIdFilter);
    if (groupBy === 'tree') p.set('group_by', 'trace');
    return p;
  };

  const toggleExpanded = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const load = async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/traces?${buildParams()}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as TraceRow[];
      setRows(data);
      if (selectedId == null && data[0]) setSelectedId(data[0].id);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const loadSummary = async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/traces/skills/summary`);
      if (!r.ok) return;
      const data = (await r.json()) as SkillSummaryRow[];
      setSummary(data);
    } catch {
      /* non-fatal */
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

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    void load();
    void loadSummary();
    void loadSources();
  }, [
    limit,
    sourceFilter,
    skillFilter,
    statusFilter,
    timeRange,
    minDuration,
    runIdFilter,
    sessionIdFilter,
  ]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => {
      void load();
      void loadSummary();
    }, 5000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    autoRefresh,
    limit,
    sourceFilter,
    skillFilter,
    statusFilter,
    timeRange,
    minDuration,
    runIdFilter,
    sessionIdFilter,
  ]);

  const selected = useMemo(
    () => rows?.find((r) => r.id === selectedId) ?? null,
    [rows, selectedId],
  );

  const toggleSkill = (name: string) => {
    setSkillFilter((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const clearAllFilters = () => {
    setSkillFilter(new Set());
    setStatusFilter('');
    setTimeRange('all');
    setMinDuration('');
    setRunIdFilter('');
    setSessionIdFilter('');
    setSourceFilter('');
  };

  async function clearAll() {
    if (!confirm('Delete every trace row? This is admin-only and irreversible.')) return;
    setClearing(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/traces`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows([]);
      setSelectedId(null);
      void loadSources();
      void loadSummary();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  }

  // Phase A fix: the chip used to read `(skillFilter.size > 0 ? 1 : 0)`,
  // so picking N skills always rendered as one filter. Sum the set size
  // so each clicked skill in the left "Skill Activity" panel ticks the
  // chip up by one.
  const activeFilterCount =
    skillFilter.size +
    (statusFilter ? 1 : 0) +
    (timeRange !== 'all' ? 1 : 0) +
    (minDuration ? 1 : 0) +
    (runIdFilter ? 1 : 0) +
    (sessionIdFilter ? 1 : 0) +
    (sourceFilter ? 1 : 0);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Hermes &middot; Ollama Traces
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Every request / response between SLM-Forge and Ollama, plus
            per-skill activity. Admin only — bodies can contain prompts,
            dataset rows, and model metadata.
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

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 text-xs">
        <label className="flex items-center gap-2 text-zinc-400">
          Status
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          >
            <option value="">all</option>
            <option value="success">success</option>
            <option value="error">error</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Time
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as TimeRange)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          >
            <option value="all">all</option>
            <option value="1h">1h</option>
            <option value="24h">24h</option>
            <option value="7d">7d</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Min ms
          <input
            type="number"
            min={0}
            value={minDuration}
            onChange={(e) => setMinDuration(e.target.value)}
            placeholder="0"
            className="w-20 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          />
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Run #
          <input
            type="number"
            min={0}
            value={runIdFilter}
            onChange={(e) => setRunIdFilter(e.target.value)}
            placeholder=""
            className="w-20 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          />
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Session #
          <input
            type="number"
            min={0}
            value={sessionIdFilter}
            onChange={(e) => setSessionIdFilter(e.target.value)}
            placeholder=""
            className="w-20 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs"
          />
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Source contains
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
        <div className="flex items-center gap-0.5 rounded-md border border-zinc-800 bg-zinc-900 p-0.5">
          <button
            onClick={() => setGroupBy('tree')}
            className={`rounded-sm px-2 py-1 text-xs ${
              groupBy === 'tree'
                ? 'bg-emerald-950/40 text-emerald-200'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
            title="Group spans by trace_id; agents render with nested skill spans"
          >
            Tree
          </button>
          <button
            onClick={() => setGroupBy('flat')}
            className={`rounded-sm px-2 py-1 text-xs ${
              groupBy === 'flat'
                ? 'bg-emerald-950/40 text-emerald-200'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
            title="One row per span (legacy view)"
          >
            Flat
          </button>
        </div>
        {activeFilterCount > 0 && (
          <>
            <button
              onClick={clearAllFilters}
              className="rounded-md border border-zinc-700 bg-zinc-800/50 px-2 py-1 text-zinc-300 hover:bg-zinc-800"
            >
              Clear filters
            </button>
            <span className="text-zinc-400">
              {activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'} active
            </span>
          </>
        )}
        {rows && (
          <span className="ml-auto text-zinc-500">
            {rows.length} matching trace{rows.length === 1 ? '' : 's'}
          </span>
        )}
      </div>

      <div
        className="grid grid-cols-[15rem_18rem_1fr] gap-3"
        style={{ minHeight: '70vh' }}
      >
        {/* Left: skill activity */}
        <aside className="overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/40">
          <div className="border-b border-zinc-800 px-3 py-2 text-[10px] uppercase tracking-wider text-zinc-500">
            Skill Activity
          </div>
          {summary.length === 0 ? (
            <div className="p-3 text-xs text-zinc-500">
              No skill traces yet. Trigger a Hermes skill from chat,
              experiments, or run-detail pages to populate.
            </div>
          ) : (
            <ul>
              {summary.map((s) => {
                const active = skillFilter.has(s.skill_name);
                const errPct =
                  s.calls > 0 ? Math.round((s.errors / s.calls) * 100) : 0;
                return (
                  <li key={s.skill_name}>
                    <button
                      onClick={() => toggleSkill(s.skill_name)}
                      className={`block w-full border-b border-zinc-800/60 px-3 py-2 text-left transition-colors ${
                        active
                          ? 'bg-emerald-950/30 text-emerald-200'
                          : 'text-zinc-300 hover:bg-zinc-800/40'
                      }`}
                      title={`current sha256: ${s.current_sha256 ?? '(none)'}`}
                    >
                      <div className="truncate font-mono text-[11px]">
                        {s.skill_name}
                      </div>
                      <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[10px] text-zinc-500">
                        <span>{s.calls} call{s.calls === 1 ? '' : 's'}</span>
                        <span
                          className={
                            errPct > 0 ? 'text-rose-400' : 'text-zinc-500'
                          }
                        >
                          {errPct}% err
                        </span>
                        <span>{s.avg_duration_ms}ms avg</span>
                      </div>
                      {s.change_count > 0 && (
                        <div className="mt-1 text-[10px] text-purple-300">
                          ✎ changed {s.change_count}×
                        </div>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        {/* Middle: list */}
        <aside className="overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/40">
          {rows === null ? (
            <div className="p-4 text-sm text-zinc-500">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="p-4 text-sm text-zinc-500">
              No traces match the current filters.
            </div>
          ) : (
            <ul>
              {rows.map((r) => (
                <li key={r.id}>
                  {r.children && r.children.length > 0 ? (
                    <TreeNode
                      row={r}
                      selectedId={selectedId}
                      onSelect={setSelectedId}
                      expanded={expanded}
                      toggleExpanded={toggleExpanded}
                    />
                  ) : (
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
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {r.skill_changed && (
                        <span className="rounded bg-purple-950/40 px-1.5 py-0.5 text-[10px] text-purple-300">
                          ✎ skill changed
                        </span>
                      )}
                      {r.run_id != null && (
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">
                          run #{r.run_id}
                        </span>
                      )}
                      {r.session_id != null && (
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">
                          sess #{r.session_id}
                        </span>
                      )}
                    </div>
                    {r.error && (
                      <div className="mt-1 truncate text-[11px] text-rose-400">
                        {r.error}
                      </div>
                    )}
                  </button>
                  )}
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
                <Stat
                  label="created"
                  value={relativeTime(selected.created_at)}
                />
                {selected.skill_name && (
                  <Stat label="skill" value={selected.skill_name} />
                )}
                {selected.skill_sha256 && (
                  <Stat
                    label="skill sha"
                    value={
                      selected.skill_changed
                        ? `${selected.skill_sha256} (changed)`
                        : selected.skill_sha256
                    }
                  />
                )}
                {selected.run_id != null && (
                  <Stat label="run" value={`#${selected.run_id}`} />
                )}
                {selected.session_id != null && (
                  <Stat label="session" value={`#${selected.session_id}`} />
                )}
              </div>
              {selected.skill_changed && selected.skill_name && (
                <div className="rounded-md bg-purple-950/20 px-3 py-2 text-xs text-purple-200">
                  Skill <span className="font-mono">{selected.skill_name}</span>
                  's content changed since the previous trace for this skill.
                  Hash now <span className="font-mono">{selected.skill_sha256}</span>.
                </div>
              )}
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

// Phase B — expandable agent row with nested skill children.
// Keyed by trace_id (or row id as fallback for legacy rows lacking one).
function TreeNode({
  row,
  selectedId,
  onSelect,
  expanded,
  toggleExpanded,
}: {
  row: TraceRow;
  selectedId: number | null;
  onSelect: (id: number) => void;
  expanded: Set<string>;
  toggleExpanded: (key: string) => void;
}) {
  const key = row.trace_id ?? String(row.id);
  const isOpen = expanded.has(key);
  const children = row.children ?? [];
  return (
    <>
      <div className={`flex w-full border-b border-zinc-800/60`}>
        <button
          onClick={() => toggleExpanded(key)}
          className="flex w-7 items-center justify-center text-zinc-500 hover:text-zinc-200"
          aria-label={isOpen ? 'Collapse' : 'Expand'}
          title={isOpen ? 'Collapse' : 'Expand'}
        >
          {isOpen ? '▾' : '▸'}
        </button>
        <button
          onClick={() => onSelect(row.id)}
          className={`flex-1 px-3 py-2 text-left transition-colors ${
            selectedId === row.id
              ? 'bg-emerald-950/30 text-emerald-200'
              : 'text-zinc-200 hover:bg-zinc-800/40'
          }`}
        >
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-sm bg-amber-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-amber-200">
              agent
            </span>
            <span className="font-mono">{row.source}</span>
            <span className="ml-auto text-zinc-500">{children.length} skill(s)</span>
            <span className="text-zinc-500">{row.duration_ms} ms</span>
          </div>
          {row.error && (
            <div className="mt-1 truncate text-[11px] text-rose-400">{row.error}</div>
          )}
        </button>
      </div>
      {isOpen &&
        children.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`block w-full border-b border-zinc-800/40 pl-10 pr-3 py-1.5 text-left transition-colors ${
              selectedId === c.id
                ? 'bg-emerald-950/30 text-emerald-200'
                : 'text-zinc-300 hover:bg-zinc-800/40'
            }`}
          >
            <div className="flex items-center gap-2 text-[11px]">
              <span className="rounded-sm bg-sky-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-sky-200">
                {c.kind ?? 'skill'}
              </span>
              <span className="font-mono">{c.skill_name || c.source}</span>
              <span className="ml-auto text-zinc-500">{c.duration_ms} ms</span>
            </div>
            {c.error && (
              <div className="mt-0.5 truncate text-[11px] text-rose-400">{c.error}</div>
            )}
          </button>
        ))}
    </>
  );
}