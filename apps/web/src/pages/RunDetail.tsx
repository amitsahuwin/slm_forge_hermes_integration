import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import HermesSkillButton, {
  type SkillResponse,
} from '../components/HermesSkillButton';
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

type AnomalyResp = {
  severity?: 'info' | 'warning' | 'critical';
  anomaly_kind?: string;
  summary?: string;
  evidence?: string[];
  recommended_action?: {
    stop_run?: boolean;
    config_changes?: Record<string, unknown>;
    reasoning?: string;
  };
};

type QuantsResp = {
  recommended_quants?: string[];
  primary?: string;
  rationale?: string;
  estimated_sizes_mb?: Record<string, number>;
  warnings?: string[];
};

const STATUS_STYLES: Record<RunStatus, string> = {
  queued: 'text-hcl-dark/60',
  running: 'text-hcl-teal',
  completed: 'text-hcl-info',
  failed: 'text-red-600',
  cancelled: 'text-hcl-dark/50',
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

  // Phase N.4 — post-mortem (markdown) + anomaly + quants
  const [postMortemRaw, setPostMortemRaw] = useState<string | null>(null);
  const [anomaly, setAnomaly] = useState<AnomalyResp | null>(null);
  const [anomalyRaw, setAnomalyRaw] = useState<string | null>(null);
  const [quants, setQuants] = useState<QuantsResp | null>(null);
  const [quantsRaw, setQuantsRaw] = useState<string | null>(null);

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

  // Compute these BEFORE any early return so React's rules-of-hooks holds —
  // useMemo below must always be called on every render or the hook order
  // changes between renders and React unmounts the component (blank page bug).
  const latestTrain = [...metrics].reverse().find((m) => m.name === 'train_loss')?.value;
  const latestVal = [...metrics].reverse().find((m) => m.name === 'val_loss')?.value;
  const latestTps = [...metrics].reverse().find((m) => m.name === 'tokens_per_sec')?.value;

  // Phase N.4 — heuristic anomaly detection: val/train ratio out of band.
  const anomalySuspected = useMemo(() => {
    if (latestTrain == null || latestVal == null || latestTrain <= 0) return false;
    const ratio = latestVal / latestTrain;
    return ratio > 1.5 || ratio < 0.6;
  }, [latestTrain, latestVal]);

  if (error) return <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>;
  if (!run) return <div className="text-sm text-hcl-dark/50">Loading run #{id}…</div>;

  const effectiveStatus = status ?? run.status;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">Run #{run.id}</h1>
          <p className="mt-1 text-sm text-hcl-dark/50">
            {run.dataset} · {run.base_model.replace(/^mlx-community\//, '')} · {run.method}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {run.status === 'completed' && run.adapter_path && (
            <>
              <HermesSkillButton
                path={`/api/v1/hermes/recommend-quants/${runId}`}
                body={{ target_device: 'iphone_pro', use_case: 'chat' }}
                label="Recommend quants"
                emoji="💡"
                tone="zinc"
                size="sm"
                onResult={(r: SkillResponse) => {
                  setQuants((r.parsed as QuantsResp) ?? null);
                  setQuantsRaw(r.parsed ? null : r.raw);
                }}
                onClear={() => {
                  setQuants(null);
                  setQuantsRaw(null);
                }}
              />
              <button
                onClick={async () => {
                  try {
                    const x = await exportsApi.create({
                      run_id: run.id,
                      quant_levels: (quants?.recommended_quants as (
                        | 'Q4_K_M'
                        | 'Q5_K_M'
                        | 'Q8_0'
                        | 'F16'
                      )[]) ?? ['Q4_K_M', 'Q5_K_M', 'Q8_0'],
                    });
                    window.location.href = `/exports`;
                    console.log('Queued export', x.id);
                  } catch (e) {
                    alert(`Failed to queue export: ${e instanceof Error ? e.message : String(e)}`);
                  }
                }}
                className="rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
              >
                Export to GGUF →
              </button>
            </>
          )}
          <div className={`font-mono text-sm ${STATUS_STYLES[effectiveStatus]}`}>● {effectiveStatus}</div>
        </div>
      </div>

      {(quants || quantsRaw) && (
        <section className="rounded-lg border border-hcl-teal/30 bg-hcl-teal/5 p-4 text-xs space-y-2">
          <h3 className="font-medium uppercase tracking-wider text-hcl-teal">
            💡 Hermes quant recommendation
          </h3>
          {quants ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {quants.recommended_quants?.map((q) => (
                  <span
                    key={q}
                    className={`rounded border px-2 py-0.5 font-mono ${
                      q === quants.primary
                        ? 'border-hcl-teal bg-hcl-teal/10 text-hcl-dark-teal'
                        : 'border-hcl-teal/30 text-hcl-dark'
                    }`}
                  >
                    {q}
                  </span>
                ))}
              </div>
              {quants.rationale && (
                <p className="italic text-hcl-dark/80">{quants.rationale}</p>
              )}
              {quants.estimated_sizes_mb && (
                <div className="grid grid-cols-4 gap-2 text-center font-mono text-[11px]">
                  {Object.entries(quants.estimated_sizes_mb).map(([k, v]) => (
                    <div
                      key={k}
                      className="rounded border border-hcl-light-blue bg-hcl-bg px-2 py-1"
                    >
                      <div className="text-hcl-dark">{Math.round(v)} MB</div>
                      <div className="text-hcl-dark/50">{k}</div>
                    </div>
                  ))}
                </div>
              )}
              {quants.warnings?.length ? (
                <ul className="rounded bg-hcl-warning/10 px-3 py-2 text-hcl-warning">
                  {quants.warnings.map((w, i) => (
                    <li key={i}>⚠ {w}</li>
                  ))}
                </ul>
              ) : null}
              <p className="text-[11px] text-hcl-dark/50">
                The "Export to GGUF" button on the right will use these quants.
              </p>
            </>
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
              {quantsRaw}
            </pre>
          )}
        </section>
      )}

      {run.error_message && (
        <div className="rounded-md bg-red-50 px-3 py-2 font-mono text-xs text-red-600">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 whitespace-pre-wrap">{run.error_message}</div>
            {run.status === 'failed' && (
              <div className="shrink-0 flex flex-col items-end gap-1">
                <button
                  type="button"
                  onClick={diagnoseWithHermes}
                  disabled={diagnosing}
                  className="rounded border border-hcl-error/40 bg-red-50 px-2 py-1 font-sans text-xs text-red-600 hover:bg-hcl-error/10 disabled:opacity-50"
                >
                  {diagnosing ? 'Asking Hermes…' : '🔬 Diagnose (OOM-focused)'}
                </button>
                <HermesSkillButton
                  path={`/api/v1/hermes/post-mortem/${runId}`}
                  label="Post-mortem (full)"
                  emoji="📋"
                  tone="rose"
                  size="sm"
                  onResult={(r: SkillResponse) => setPostMortemRaw(r.raw)}
                  onClear={() => setPostMortemRaw(null)}
                />
              </div>
            )}
          </div>
          {diagnosisError && (
            <div className="mt-2 text-hcl-warning">⚠ {diagnosisError}</div>
          )}
        </div>
      )}

      {postMortemRaw && (
        <section className="rounded-lg border border-hcl-error/40 bg-red-50 p-4 text-xs">
          <h3 className="mb-2 font-medium uppercase tracking-wider text-red-600">
            📋 Failure post-mortem (also saved to runs/{runId}/post_mortem.md)
          </h3>
          <pre className="whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-hcl-dark">
            {postMortemRaw}
          </pre>
        </section>
      )}

      {(diagnosis || diagnosisRaw) && (
        <section className="rounded-lg border border-hcl-warning/40 bg-hcl-warning/10 p-4 space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-hcl-warning">
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
                <p className="text-xs italic text-hcl-dark/80">{diagnosis.reasoning}</p>
              )}
              {diagnosis.expected_outcome && (
                <p className="text-xs text-hcl-dark/60">
                  <span className="font-medium text-hcl-dark/80">Expected:</span>{' '}
                  {diagnosis.expected_outcome}
                </p>
              )}
            </>
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
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

      {/* Phase N.4 — anomaly chip when val/train ratio is out of band */}
      {anomalySuspected && (
        <div className="flex items-center gap-2 rounded-md border border-hcl-warning/40 bg-hcl-warning/10 px-3 py-2 text-xs text-hcl-warning">
          <span>
            Heuristic flag: val/train ratio ={' '}
            <span className="font-mono">
              {latestVal != null && latestTrain != null && latestTrain > 0
                ? (latestVal / latestTrain).toFixed(2)
                : '—'}
            </span>{' '}
            — outside the 0.6–1.5 healthy band.
          </span>
          <HermesSkillButton
            path={`/api/v1/hermes/explain-anomaly/${runId}`}
            label="Explain anomaly"
            emoji="🔬"
            tone="amber"
            size="sm"
            onResult={(r: SkillResponse) => {
              setAnomaly((r.parsed as AnomalyResp) ?? null);
              setAnomalyRaw(r.parsed ? null : r.raw);
            }}
            onClear={() => {
              setAnomaly(null);
              setAnomalyRaw(null);
            }}
          />
        </div>
      )}

      {(anomaly || anomalyRaw) && (
        <section className="rounded-lg border border-hcl-warning/40 bg-hcl-warning/10 p-4 space-y-2 text-xs">
          <div className="flex items-baseline justify-between">
            <h3 className="font-medium uppercase tracking-wider text-hcl-warning">
              🔬 Anomaly explanation
            </h3>
            {anomaly?.severity && (
              <span
                className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                  anomaly.severity === 'critical'
                    ? 'bg-red-50 text-red-600'
                    : anomaly.severity === 'warning'
                    ? 'bg-hcl-warning/10 text-hcl-warning'
                    : 'bg-hcl-tech-grey text-hcl-dark/60'
                }`}
              >
                {anomaly.anomaly_kind ?? anomaly.severity}
              </span>
            )}
          </div>
          {anomaly?.summary && <p className="italic text-hcl-dark/80">{anomaly.summary}</p>}
          {anomaly?.evidence?.length ? (
            <ul className="list-disc pl-5 text-hcl-dark/60">
              {anomaly.evidence.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          ) : null}
          {anomaly?.recommended_action && (
            <div className="rounded bg-hcl-bg px-3 py-2">
              <div className="font-mono text-[11px] text-hcl-dark/80">
                {Object.entries(anomaly.recommended_action.config_changes ?? {}).map(
                  ([k, v]) => (
                    <div key={k}>
                      <span className="text-hcl-dark/50">{k}:</span>{' '}
                      <span className="text-hcl-dark">{String(v)}</span>
                    </div>
                  ),
                )}
              </div>
              {anomaly.recommended_action.reasoning && (
                <p className="mt-1 italic text-hcl-dark/60">
                  {anomaly.recommended_action.reasoning}
                </p>
              )}
            </div>
          )}
          {!anomaly && anomalyRaw && (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
              {anomalyRaw}
            </pre>
          )}
        </section>
      )}

      <LiveLossChart metrics={metrics} />

      {streamError && <div className="font-mono text-xs text-hcl-dark/40">stream: {streamError}</div>}

      <section>
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Training log
        </h3>
        <LogPane runId={runId} height="22rem" />
      </section>

      <section className="rounded-lg border border-hcl-light-blue bg-white p-4">
        <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Configuration
        </h3>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={run.base_model} />
          <Row label="trainer_backend" value={run.trainer_backend ?? 'mlx'} />
          {run.claimed_by && <Row label="claimed_by" value={run.claimed_by} />}
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
    <div className="rounded-lg border border-hcl-light-blue bg-white px-3 py-2.5">
      <div className="font-mono text-xs text-hcl-dark/50">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-hcl-dark">{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-hcl-dark/50">{label}</dt>
      <dd className="truncate text-hcl-dark/80">{value}</dd>
    </>
  );
}

function DiagRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-hcl-dark/50">{label}</span>
      <span className="text-hcl-dark">{value}</span>
    </>
  );
}
