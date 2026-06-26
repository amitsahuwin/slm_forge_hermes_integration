/**
 * PR-C — Auto-Fixes admin tab.
 *
 * Surfaces every ``AutoFixAttempt`` row (PR-A scaffold + PR-B dev-mode
 * loop). Admin-only — route is wrapped in <RequireRole role="admin"> in
 * App.tsx. The endpoints (/api/v1/autofix/*) enforce the same gate
 * server-side via the existing OPA policy.
 *
 * Layout:
 *   - Top: stats panel (total + group-bys).
 *   - Left: filter bar + paginated list.
 *   - Right: detail drawer (diff, correlation IDs, abandon action).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  autofix as autofixApi,
  AutoFixDetail,
  AutoFixRow,
  AutoFixStats,
  AutoFixStatus,
} from '../lib/api';

const STATUSES: (AutoFixStatus | '')[] = [
  '',
  'reported',
  'proposed',
  'applied',
  'verified',
  'deployed',
  'rejected',
  'failed',
];

const STATUS_COLOR: Record<AutoFixStatus, string> = {
  reported: 'bg-hcl-warning/100/10 text-hcl-warning border-hcl-warning/50',
  proposed: 'bg-hcl-info/10 text-hcl-info border-hcl-info/40',
  applied: 'bg-hcl-info/10 text-hcl-info border-hcl-info/40',
  verified: 'bg-hcl-teal/10 text-hcl-teal border-hcl-teal/30',
  deployed: 'bg-hcl-dark-teal/15 text-hcl-dark-teal border-hcl-teal',
  rejected: 'bg-hcl-light-blue/50 text-hcl-dark/80 border-hcl-teal/30',
  failed: 'bg-hcl-error/10 text-hcl-error border-hcl-error/50',
};

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

function StatusBadge({ status }: { status: AutoFixStatus | string }) {
  const cls =
    STATUS_COLOR[status as AutoFixStatus] ?? 'bg-hcl-light-blue text-hcl-dark border-hcl-teal/30';
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cls}`}
    >
      {status}
    </span>
  );
}

function StatsPanel({ stats }: { stats: AutoFixStats | null }) {
  if (!stats) {
    return (
      <div className="rounded border border-hcl-light-blue bg-white p-3 text-xs text-hcl-dark/50">
        Loading stats…
      </div>
    );
  }
  const groups: Array<[string, Record<string, number>]> = [
    ['By status', stats.by_status],
    ['By source', stats.by_source],
    ['By mode', stats.by_mode],
  ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
      <div className="rounded border border-hcl-light-blue bg-white p-3">
        <div className="text-[11px] uppercase tracking-wide text-hcl-dark/50">Total</div>
        <div className="mt-1 text-2xl font-semibold text-hcl-dark">{stats.total}</div>
      </div>
      {groups.map(([label, dict]) => (
        <div key={label} className="rounded border border-hcl-light-blue bg-white p-3">
          <div className="text-[11px] uppercase tracking-wide text-hcl-dark/50">{label}</div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {Object.entries(dict)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 6)
              .map(([k, v]) => (
                <li key={k} className="flex justify-between">
                  <span className="text-hcl-dark/80">{k}</span>
                  <span className="text-hcl-dark/50">{v}</span>
                </li>
              ))}
            {Object.keys(dict).length === 0 && (
              <li className="text-hcl-dark/40">none</li>
            )}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Detail({
  attempt,
  onAbandon,
  abandoning,
}: {
  attempt: AutoFixDetail | null;
  onAbandon: () => void;
  abandoning: boolean;
}) {
  if (!attempt) {
    return (
      <div className="rounded border border-hcl-light-blue bg-white p-6 text-sm text-hcl-dark/50">
        Select a row on the left to inspect.
      </div>
    );
  }
  const canAbandon = !['rejected', 'deployed'].includes(attempt.status);
  return (
    <div className="space-y-3 rounded border border-hcl-light-blue bg-white p-4">
      <div className="flex items-center gap-2">
        <StatusBadge status={attempt.status} />
        <span className="text-sm font-mono text-hcl-dark/80">{attempt.error_type}</span>
        <span className="ml-auto text-[11px] text-hcl-dark/50">
          {relativeTime(attempt.created_at)}
        </span>
      </div>
      <div className="text-xs text-hcl-dark/60">
        <div className="break-words font-mono">{attempt.error_message || '(empty)'}</div>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-[11px]">
        <Field label="fingerprint" value={attempt.fingerprint.slice(0, 12)} mono />
        <Field label="mode" value={attempt.mode} />
        <Field label="source" value={attempt.source} />
        <Field label="file_target" value={attempt.file_target ?? '—'} mono />
        <Field label="branch" value={attempt.branch ?? '—'} mono />
        <Field label="test_path" value={attempt.test_path ?? '—'} mono />
        <Field
          label="issue_url"
          value={
            attempt.issue_url ? (
              <a
                href={attempt.issue_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-hcl-info underline"
              >
                {attempt.issue_url}
              </a>
            ) : (
              '—'
            )
          }
        />
        <Field label="tenant_id" value={attempt.tenant_id} />
        <Field label="occurrences (60s window)" value={String(attempt.occurrences_in_window)} />
        <Field label="attempt #" value={String(attempt.attempt_count)} />
      </dl>
      <div className="rounded border border-hcl-light-blue bg-hcl-bg p-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-hcl-dark/50">
          Correlation IDs
        </div>
        <div className="text-[11px] text-hcl-dark/60">
          {attempt.correlation_request_id ||
          attempt.correlation_run_id ||
          attempt.correlation_session_id ? (
            <ul className="space-y-0.5 font-mono">
              {attempt.correlation_request_id && (
                <li>request: {attempt.correlation_request_id}</li>
              )}
              {attempt.correlation_run_id && (
                <li>run: {attempt.correlation_run_id}</li>
              )}
              {attempt.correlation_session_id && (
                <li>session: {attempt.correlation_session_id}</li>
              )}
            </ul>
          ) : (
            <span className="text-hcl-dark/40">(none captured)</span>
          )}
        </div>
      </div>
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-hcl-dark/50">
          Diff (truncated)
        </div>
        <pre className="max-h-[400px] overflow-auto rounded border border-hcl-light-blue bg-hcl-bg p-2 text-[11px] leading-snug text-hcl-dark/80">
          {attempt.diff || '(no diff captured)'}
        </pre>
      </div>
      {canAbandon && (
        <div>
          <button
            type="button"
            onClick={onAbandon}
            disabled={abandoning}
            className="rounded border border-hcl-error/50 bg-hcl-error/10 px-2.5 py-1 text-[12px] font-medium text-hcl-error hover:bg-red-500/20 disabled:opacity-50"
          >
            {abandoning ? 'Abandoning…' : 'Abandon (mark rejected)'}
          </button>
          <span className="ml-2 text-[10px] text-hcl-dark/50">
            Stops the 24-hour auto-retry window for this fingerprint.
          </span>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="rounded border border-hcl-light-blue bg-hcl-bg px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-hcl-dark/50">{label}</div>
      <div
        className={`mt-0.5 break-words text-hcl-dark/80 ${mono ? 'font-mono text-[11px]' : 'text-[12px]'}`}
      >
        {value}
      </div>
    </div>
  );
}

export default function AutoFixes() {
  const [rows, setRows] = useState<AutoFixRow[] | null>(null);
  const [stats, setStats] = useState<AutoFixStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<AutoFixStatus | ''>('');
  const [fingerprintFilter, setFingerprintFilter] = useState<string>('');
  const [limit, setLimit] = useState<number>(50);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AutoFixDetail | null>(null);
  const [abandoning, setAbandoning] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await autofixApi.list({
        status: statusFilter || undefined,
        fingerprint: fingerprintFilter || undefined,
        limit,
      });
      setRows(r);
      setError(null);
      if (selectedId == null && r[0]) setSelectedId(r[0].id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [statusFilter, fingerprintFilter, limit, selectedId]);

  const loadStats = useCallback(async () => {
    try {
      const s = await autofixApi.stats();
      setStats(s);
    } catch {
      /* non-fatal */
    }
  }, []);

  // Initial load + filter-triggered reloads.
  useEffect(() => {
    void load();
    void loadStats();
  }, [load, loadStats]);

  // Detail loading whenever selection changes.
  useEffect(() => {
    let cancelled = false;
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    autofixApi.get(selectedId).then(
      (d) => {
        if (!cancelled) setDetail(d);
      },
      (e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // Light polling — pending attempts move through states quickly in dev mode.
  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(() => {
      void load();
      void loadStats();
    }, 5_000);
    return () => window.clearInterval(id);
  }, [autoRefresh, load, loadStats]);

  const onAbandon = async () => {
    if (selectedId == null) return;
    setAbandoning(true);
    try {
      await autofixApi.abandon(selectedId);
      await load();
      const fresh = await autofixApi.get(selectedId);
      setDetail(fresh);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAbandoning(false);
    }
  };

  const rowCount = rows?.length ?? 0;
  const headerCount = useMemo(() => {
    if (!rows) return '…';
    return `${rowCount} ${rowCount === 1 ? 'attempt' : 'attempts'}`;
  }, [rows, rowCount]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Auto-Fixes</h1>
        <p className="mt-1 text-sm text-hcl-dark/60">
          Every captured error + auto-fix attempt across the API and workers.
          Admin-only. Status transitions: reported → proposed → applied →
          verified → deployed, or rejected / failed.
        </p>
      </div>

      <StatsPanel stats={stats} />

      <div className="flex flex-wrap items-center gap-2 rounded border border-hcl-light-blue bg-white p-3">
        <label className="text-xs text-hcl-dark/60">Status</label>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as AutoFixStatus | '')}
          className="rounded border border-hcl-light-blue bg-hcl-bg px-2 py-1 text-xs text-hcl-dark"
        >
          {STATUSES.map((s) => (
            <option key={s || 'all'} value={s}>
              {s || 'all'}
            </option>
          ))}
        </select>
        <label className="text-xs text-hcl-dark/60">Fingerprint</label>
        <input
          type="text"
          value={fingerprintFilter}
          onChange={(e) => setFingerprintFilter(e.target.value.trim())}
          placeholder="sha256 (full)"
          className="w-56 rounded border border-hcl-light-blue bg-hcl-bg px-2 py-1 font-mono text-[11px] text-hcl-dark"
        />
        <label className="text-xs text-hcl-dark/60">Limit</label>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded border border-hcl-light-blue bg-hcl-bg px-2 py-1 text-xs text-hcl-dark"
        >
          {[25, 50, 100, 200, 500].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <label className="ml-auto flex items-center gap-1 text-xs text-hcl-dark/60">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="accent-hcl-teal"
          />
          auto-refresh (5s)
        </label>
        <span className="text-[11px] text-hcl-dark/50">{headerCount}</span>
      </div>

      {error && (
        <div className="rounded border border-hcl-error/40 bg-hcl-error/10 p-3 text-xs text-hcl-error">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_1fr]">
        <div className="rounded border border-hcl-light-blue bg-white">
          <table className="w-full table-fixed text-xs">
            <thead className="bg-hcl-dark-blue text-left text-[10px] uppercase tracking-wide text-white">
              <tr>
                <th className="w-16 px-2 py-1.5">id</th>
                <th className="px-2 py-1.5">error · file</th>
                <th className="w-20 px-2 py-1.5">status</th>
                <th className="w-14 px-2 py-1.5">age</th>
              </tr>
            </thead>
            <tbody>
              {rows == null && (
                <tr>
                  <td colSpan={4} className="px-2 py-3 text-hcl-dark/50">
                    Loading…
                  </td>
                </tr>
              )}
              {rows && rows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-2 py-3 text-hcl-dark/50">
                    No attempts yet. Errors captured by the responder will appear here.
                  </td>
                </tr>
              )}
              {rows?.map((row) => {
                const selected = row.id === selectedId;
                return (
                  <tr
                    key={row.id}
                    onClick={() => setSelectedId(row.id)}
                    className={`cursor-pointer border-t border-hcl-light-blue hover:bg-hcl-tech-grey ${
                      selected ? 'bg-hcl-tech-grey/60' : ''
                    }`}
                  >
                    <td className="px-2 py-1.5 font-mono text-hcl-dark/50">#{row.id}</td>
                    <td className="truncate px-2 py-1.5">
                      <div className="truncate font-mono text-[11px] text-hcl-dark/80">
                        {row.error_type}
                      </div>
                      <div className="truncate text-[10px] text-hcl-dark/50">
                        {row.file_target ?? row.source}
                      </div>
                    </td>
                    <td className="px-2 py-1.5">
                      <StatusBadge status={row.status} />
                    </td>
                    <td className="px-2 py-1.5 text-[10px] text-hcl-dark/50">
                      {relativeTime(row.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <Detail attempt={detail} onAbandon={onAbandon} abandoning={abandoning} />
      </div>
    </div>
  );
}
