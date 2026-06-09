import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import LiveLossChart from '../components/ratchet/LiveLossChart';
import LogPane from '../components/LogPane';
import { useRunMetrics } from '../hooks/useRunMetrics';
import { API_URL, type Run, type RunStatus, api, exportsApi } from '../lib/api';

type HermesDiagnosis = {
  batch_size?: number;
  max_seq_length?: number;
  num_layers?: number;
  grad_checkpoint?: boolean;
  learning_rate?: number;
  reasoning?: string;
  expected_outcome?: string;
};

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const runId = id ? parseInt(id, 10) : undefined;
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { metrics, status, error: streamError } = useRunMetrics(runId);

  // Phase N.1 — Diagnose-with-Hermes state
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState<HermesDiagnosis | null>(null);
  const [diagnosisRaw, setDiagnosisRaw] = useState<string | null>(null);
  const [diagnosisError, setDiagnosisError] = useState<string | null>(null);

  async function diagnoseWithHermes() {
    if (!runId) return;
    setDiagnosing(true);
    setDiagnosisError(null);
    setDiagnosis(null);
    setDiagnosisRaw(null);
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/diagnose-run/${runId}`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        parsed: HermesDiagnosis | null;
        raw: string;
      };
      setDiagnosis(data.parsed);
      setDiagnosisRaw(data.raw);
    } catch (e: unknown) {
      setDiagnosisError(e instanceof Error ? e.message : String(e));
    } finally {
      setDiagnosing(false);
    }
  }

  useEffect(() => {
    if (runId === undefined) return;
    let alive = true;
    const tick = () => {
      api
        .getRun(runId)
        .then((r) => alive && setRun(r))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [runId]);

  if (error) return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!run) return <div className="text-sm text-zinc-500">Loading run #{id}…</div>;

  const effectiveStatus = status ?? run.status;
  const latestTrain = [...metrics].reverse().find((m) => m.name === 'train_loss')?.value;
  const latestVal = [...metrics].reverse().find((m) => m.name === 'val_loss')?.value;
  const latestTps = [...metrics].reverse().find((m) => m.name === 'tokens_per_sec')?.value;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">Run #{run.id}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {run.dataset} · {run.base_model.replace(/^mlx-community\//, '')} · {run.method}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {run.status === 'completed' && run.adapter_path && (
            <button
              onClick={async () => {
                try {
                  const x = await exportsApi.create({ run_id: run.id, quant_levels: ['Q4_K_M', 'Q8_0'] });
                  window.location.href = `/exports`;
                  console.log('Queued export', x.id);
                } catch (e) {
                  alert(`Failed to queue export: ${e instanceof Error ? e.message : String(e)}`);
                }
              }}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
            >
              Export to GGUF →
            </button>
          )}
          <div className={`font-mono text-sm ${STATUS_STYLES[effectiveStatus]}`}>● {effectiveStatus}</div>
        </div>
      </div>

      {run.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 whitespace-pre-wrap">{run.error_message}</div>
            {run.status === 'failed' && (
              <button
                type="button"
                onClick={diagnoseWithHermes}
                disabled={diagnosing}
                className="shrink-0 rounded border border-rose-700/60 bg-rose-900/40 px-2 py-1 font-sans text-xs text-rose-100 hover:bg-rose-800/50 disabled:opacity-50"
              >
                {diagnosing ? 'Asking Hermes…' : '🔬 Diagnose with Hermes'}
              </button>
            )}
          </div>
          {diagnosisError && (
            <div className="mt-2 text-amber-300">⚠ {diagnosisError}</div>
          )}
        </div>
      )}

      {(diagnosis || diagnosisRaw) && (
        <section className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-4 space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-amber-300">
            🔬 Hermes diagnosis
          </h3>
          {diagnosis ? (
            <>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
                {diagnosis.batch_size != null && (
                  <DiagRow label="batch_size" value={String(diagnosis.batch_size)} />
                )}
                {diagnosis.max_seq_length != null && (
                  <DiagRow label="max_seq_length" value={String(diagnosis.max_seq_length)} />
                )}
                {diagnosis.num_layers != null && (
                  <DiagRow label="num_layers" value={String(diagnosis.num_layers)} />
                )}
                {diagnosis.grad_checkpoint != null && (
                  <DiagRow label="grad_checkpoint" value={String(diagnosis.grad_checkpoint)} />
                )}
                {diagnosis.learning_rate != null && (
                  <DiagRow
                    label="learning_rate"
                    value={diagnosis.learning_rate.toExponential(2)}
                  />
                )}
              </div>
              {diagnosis.reasoning && (
                <p className="text-xs italic text-zinc-300">{diagnosis.reasoning}</p>
              )}
              {diagnosis.expected_outcome && (
                <p className="text-xs text-zinc-400">
                  <span className="font-medium text-zinc-300">Expected:</span>{' '}
                  {diagnosis.expected_outcome}
                </p>
              )}
            </>
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-zinc-300">
              {diagnosisRaw}
            </pre>
          )}
        </section>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="train loss" value={latestTrain?.toFixed(4) ?? '—'} />
        <Stat label="val loss" value={latestVal?.toFixed(4) ?? '—'} />
        <Stat label="tokens/sec" value={latestTps?.toFixed(0) ?? '—'} />
        <Stat label="iters" value={`${countSteps(metrics)} / ${run.iters}`} />
      </section>

      <LiveLossChart metrics={metrics} />

      {streamError && <div className="font-mono text-xs text-zinc-600">stream: {streamError}</div>}

      <section>
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
          Training log
        </h3>
        <LogPane runId={runId} height="22rem" />
      </section>

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-zinc-500">
          Configuration
        </h3>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={run.base_model} />
          <Row label="method" value={run.method} />
          <Row label="iters" value={String(run.iters)} />
          <Row label="batch_size" value={String(run.batch_size)} />
          <Row label="learning_rate" value={run.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(run.num_layers)} />
          <Row label="max_seq_length" value={String(run.max_seq_length)} />
          <Row label="grad_checkpoint" value={String(run.grad_checkpoint)} />
          <Row label="seed" value={String(run.seed)} />
        </dl>
      </section>
    </div>
  );
}

function countSteps(metrics: { step: number; name: string }[]): number {
  const steps = new Set<number>();
  for (const m of metrics) if (m.name === 'train_loss') steps.add(m.step);
  return steps.size > 0 ? Math.max(...steps) : 0;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
      <div className="font-mono text-xs text-zinc-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-zinc-100">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-zinc-500">{label}</dt>
      <dd className="truncate text-zinc-300">{value}</dd>
    </>
  );
}

function DiagRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-zinc-500">{label}</span>
      <span className="text-zinc-100">{value}</span>
    </>
  );
}
