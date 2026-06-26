import { useEffect, useState } from 'react';
import { admin, type CleanupPlan, type DiskUsageResponse } from '../lib/api';

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let val = n / 1024;
  let u = 0;
  while (val >= 1024 && u < units.length - 1) {
    val /= 1024;
    u++;
  }
  return `${val.toFixed(val > 10 ? 0 : 1)} ${units[u]}`;
}

export default function Maintenance() {
  const [usage, setUsage] = useState<DiskUsageResponse | null>(null);
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [u, p] = await Promise.all([admin.diskUsage(), admin.cleanupPlan()]);
      setUsage(u);
      setPlan(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
    const iv = window.setInterval(refresh, 5000);
    return () => window.clearInterval(iv);
  }, []);

  async function doCleanup() {
    if (!plan || plan.rejected_runs.length === 0) return;
    if (!confirm(
      `Delete ${plan.rejected_runs.length} rejected iteration artifacts? ` +
      `Frees ~${humanBytes(plan.bytes_freed_estimate)}. DB rows are kept.`
    )) return;
    setBusy(true);
    try {
      const r = await admin.cleanupExecute();
      setLastResult(
        `✓ Deleted ${r.deleted_run_ids.length} runs · freed ${humanBytes(r.bytes_freed)}`
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Maintenance</h1>
        <p className="mt-1 text-sm text-hcl-dark/50">
          Disk usage and cleanup actions.
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>
      )}

      {lastResult && (
        <div className="rounded-md bg-hcl-teal/10 px-3 py-2 text-sm text-hcl-teal">
          {lastResult}
        </div>
      )}

      <section className="rounded-lg border border-hcl-light-blue bg-white p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Disk usage
        </h3>
        {usage === null ? (
          <div className="text-sm text-hcl-dark/50">Loading…</div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="bg-hcl-dark-blue text-xs uppercase tracking-wider text-white">
                <tr>
                  <th className="px-3 py-1.5 text-left">Location</th>
                  <th className="px-3 py-1.5 text-left font-mono">Path</th>
                  <th className="px-3 py-1.5 text-right">Items</th>
                  <th className="px-3 py-1.5 text-right">Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hcl-light-blue">
                {usage.entries.map((e) => (
                  <tr key={e.label} className="font-mono text-hcl-dark/80">
                    <td className="px-3 py-2">{e.label}</td>
                    <td className="px-3 py-2 text-xs text-hcl-dark/50">{e.path}</td>
                    <td className="px-3 py-2 text-right text-hcl-dark/60">{e.items}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{humanBytes(e.bytes)}</td>
                  </tr>
                ))}
                <tr className="border-t-2 border-hcl-teal/30 font-mono">
                  <td className="px-3 py-2 font-semibold text-hcl-dark" colSpan={3}>Total</td>
                  <td className="px-3 py-2 text-right text-hcl-teal tabular-nums">
                    {humanBytes(usage.total_bytes)}
                  </td>
                </tr>
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="rounded-lg border border-hcl-light-blue bg-white p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Cleanup: rejected iterations
        </h3>
        {plan === null ? (
          <div className="text-sm text-hcl-dark/50">Loading…</div>
        ) : (
          <>
            <p className="mb-4 text-sm text-hcl-dark/60">{plan.description}</p>
            {plan.rejected_runs.length === 0 ? (
              <div className="text-sm text-hcl-dark/50">
                Nothing to clean up. (No rejected iterations from completed sessions found.)
              </div>
            ) : (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3">
                  <div className="rounded-md border border-hcl-light-blue px-3 py-2">
                    <div className="font-mono text-xs text-hcl-dark/50">candidates</div>
                    <div className="mt-1 font-mono text-lg text-hcl-dark">
                      {plan.rejected_runs.length} runs
                    </div>
                  </div>
                  <div className="rounded-md border border-hcl-light-blue px-3 py-2">
                    <div className="font-mono text-xs text-hcl-dark/50">estimated free</div>
                    <div className="mt-1 font-mono text-lg text-hcl-teal">
                      {humanBytes(plan.bytes_freed_estimate)}
                    </div>
                  </div>
                </div>
                <details className="mb-3 text-xs">
                  <summary className="cursor-pointer text-hcl-dark/50 hover:text-hcl-dark/80">
                    Show run IDs ({plan.rejected_runs.length})
                  </summary>
                  <div className="mt-2 font-mono text-hcl-dark/60">
                    {plan.rejected_runs.join(', ')}
                  </div>
                </details>
                <button
                  onClick={doCleanup}
                  disabled={busy}
                  className="rounded-md bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-hcl-light-blue"
                >
                  {busy ? 'Cleaning…' : `Delete ${plan.rejected_runs.length} rejected iterations`}
                </button>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
