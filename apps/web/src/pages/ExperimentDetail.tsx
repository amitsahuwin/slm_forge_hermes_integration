import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import CanaryDriftChart from '../components/ratchet/CanaryDriftChart';
import HyperparamHeatmap from '../components/ratchet/HyperparamHeatmap';
import IterationTable from '../components/ratchet/IterationTable';
import RatchetTimeline from '../components/ratchet/RatchetTimeline';
import {
  API_URL,
  type Run,
  type SessionStatus,
  type TrainingSession as Experiment,
  api,
} from '../lib/api';

type HermesDriftAnalysis = {
  learning_rate?: number;
  num_layers?: number;
  reasoning?: string;
  expected_outcome?: string;
};

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-hcl-dark/60',
  running: 'text-hcl-teal',
  completed: 'text-hcl-info',
  failed: 'text-red-600',
  cancelled: 'text-hcl-dark/50',
};

export default function ExperimentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const sid = id ? parseInt(id, 10) : undefined;
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [iterations, setIterations] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);

  // Phase N.1 — Hermes analyze_canary_drift
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<HermesDriftAnalysis | null>(null);
  const [analysisRaw, setAnalysisRaw] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  // Compute max observed drift across iterations to enable/highlight the button.
  const maxDrift = useMemo(() => {
    const drifts = iterations
      .map((it) =>
        it.final_val_loss != null && it.canary_loss != null
          ? Math.abs(it.canary_loss - it.final_val_loss)
          : null,
      )
      .filter((v): v is number => v != null);
    return drifts.length > 0 ? Math.max(...drifts) : null;
  }, [iterations]);
  const hasAnyCanary = maxDrift != null;
  const exceedsThreshold =
    hasAnyCanary && !!experiment && maxDrift > experiment.canary_drift_threshold;

  async function rerun() {
    if (sid === undefined) return;
    setRerunning(true);
    setRerunError(null);
    try {
      const next = await api.rerunSession(sid);
      navigate(`/experiments/${next.id}`);
    } catch (e: unknown) {
      setRerunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRerunning(false);
    }
  }

  async function analyzeDrift() {
    if (sid === undefined) return;
    setAnalyzing(true);
    setAnalysisError(null);
    setAnalysis(null);
    setAnalysisRaw(null);
    try {
      const r = await fetch(`${API_URL}/api/v1/hermes/analyze-drift/${sid}`, {
        method: 'POST',
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        parsed: HermesDriftAnalysis | null;
        raw: string;
      };
      setAnalysis(data.parsed);
      setAnalysisRaw(data.raw);
    } catch (e: unknown) {
      setAnalysisError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  }

  useEffect(() => {
    if (sid === undefined) return;
    let alive = true;
    const tick = async () => {
      try {
        const [s, its] = await Promise.all([api.getSession(sid), api.listIterations(sid)]);
        if (alive) {
          setExperiment(s);
          setIterations(its);
        }
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => {
      alive = false;
      window.clearInterval(iv);
    };
  }, [sid]);

  if (error)
    return <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>;
  if (!experiment) return <div className="text-sm text-hcl-dark/50">Loading experiment #{id}…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{experiment.name}</h1>
          <p className="mt-1 text-sm text-hcl-dark/50">
            Experiment #{experiment.id} · {experiment.dataset} ·{' '}
            {experiment.base_model.replace(/^mlx-community\//, '')} · {experiment.method}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {(experiment.status === 'failed' ||
            experiment.status === 'completed' ||
            experiment.status === 'cancelled') && (
            <button
              type="button"
              onClick={rerun}
              disabled={rerunning}
              className="rounded border border-hcl-teal/30 bg-hcl-teal/10 px-3 py-1.5 text-xs font-medium text-hcl-dark-teal hover:bg-hcl-teal/15 disabled:opacity-50"
              title="Clone this experiment's config into a new queued experiment"
            >
              {rerunning ? 'Rerunning…' : '↻ Rerun'}
            </button>
          )}
          <div className={`font-mono text-sm ${STATUS_STYLES[experiment.status]}`}>
            ● {experiment.status}
          </div>
        </div>
      </div>

      {rerunError && (
        <div className="rounded-md bg-red-50 px-3 py-2 font-mono text-xs text-red-600">
          Rerun failed: {rerunError}
        </div>
      )}

      {experiment.error_message && (
        <div className="rounded-md bg-red-50 px-3 py-2 font-mono text-xs text-red-600">
          {experiment.error_message}
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="round"
          value={`${experiment.current_round + 1} / ${experiment.max_rounds}`}
        />
        <Stat
          label="best metric"
          value={
            experiment.best_metric_value !== null
              ? experiment.best_metric_value.toFixed(4)
              : '—'
          }
        />
        <Stat
          label="best run"
          value={
            experiment.best_run_id !== null ? (
              <Link
                to={`/runs/${experiment.best_run_id}`}
                className="text-hcl-teal hover:underline"
              >
                #{experiment.best_run_id}
              </Link>
            ) : (
              '—'
            )
          }
        />
        <Stat
          label="accepted"
          value={`${iterations.filter((i) => i.was_accepted).length} / ${iterations.length}`}
        />
      </section>

      <RatchetTimeline iterations={iterations} targetMetric={experiment.target_metric} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <HyperparamHeatmap iterations={iterations} />
        <div className="space-y-3">
          <CanaryDriftChart
            iterations={iterations}
            threshold={experiment.canary_drift_threshold}
          />
          {hasAnyCanary && (
            <div className="flex items-center justify-between gap-3 rounded-md border border-hcl-light-blue bg-white px-3 py-2 text-xs">
              <div className="text-hcl-dark/60">
                Max observed drift:{' '}
                <span
                  className={`font-mono ${
                    exceedsThreshold ? 'text-hcl-warning' : 'text-hcl-teal'
                  }`}
                >
                  {maxDrift!.toFixed(3)}
                </span>{' '}
                <span className="text-hcl-dark/50">
                  / threshold {experiment.canary_drift_threshold.toFixed(2)}
                </span>
              </div>
              <button
                type="button"
                onClick={analyzeDrift}
                disabled={analyzing}
                className={`rounded border px-2.5 py-1 text-xs hover:bg-hcl-tech-grey disabled:opacity-50 ${
                  exceedsThreshold
                    ? 'border-hcl-warning/50 bg-hcl-warning/10 text-hcl-warning'
                    : 'border-hcl-teal/30 text-hcl-dark'
                }`}
              >
                {analyzing ? 'Analyzing…' : '🔬 Analyze drift'}
              </button>
            </div>
          )}
          {analysisError && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">
              {analysisError}
            </div>
          )}
          {(analysis || analysisRaw) && (
            <div className="rounded-md border border-hcl-warning/40 bg-hcl-warning/10 p-3 text-xs space-y-2">
              <h4 className="font-medium uppercase tracking-wider text-hcl-warning">
                🔬 Hermes drift analysis
              </h4>
              {analysis ? (
                <>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono">
                    {analysis.learning_rate != null && (
                      <>
                        <span className="text-hcl-dark/50">learning_rate</span>
                        <span className="text-hcl-dark">
                          {analysis.learning_rate.toExponential(2)}
                        </span>
                      </>
                    )}
                    {analysis.num_layers != null && (
                      <>
                        <span className="text-hcl-dark/50">num_layers</span>
                        <span className="text-hcl-dark">{analysis.num_layers}</span>
                      </>
                    )}
                  </div>
                  {analysis.reasoning && (
                    <p className="italic text-hcl-dark/80">{analysis.reasoning}</p>
                  )}
                  {analysis.expected_outcome && (
                    <p className="text-hcl-dark/60">
                      <span className="font-medium text-hcl-dark/80">Expected:</span>{' '}
                      {analysis.expected_outcome}
                    </p>
                  )}
                </>
              ) : (
                <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
                  {analysisRaw}
                </pre>
              )}
            </div>
          )}
        </div>
      </div>

      <section className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-hcl-dark/50">Iterations</h3>
        {iterations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-8 text-center text-sm text-hcl-dark/50">
            Waiting for ratchet worker to create the first iteration.
          </div>
        ) : (
          <IterationTable iterations={iterations} />
        )}
      </section>

      <details className="rounded-lg border border-hcl-light-blue bg-white p-4">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Experiment configuration
        </summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={experiment.base_model} />
          <Row label="method" value={experiment.method} />
          <Row label="iters" value={String(experiment.iters)} />
          <Row label="batch_size" value={String(experiment.batch_size)} />
          <Row label="learning_rate" value={experiment.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(experiment.num_layers)} />
          <Row label="max_seq_length" value={String(experiment.max_seq_length)} />
          <Row label="max_rounds" value={String(experiment.max_rounds)} />
          <Row label="plateau_patience" value={String(experiment.plateau_patience)} />
          <Row label="min_delta" value={String(experiment.min_delta)} />
          <Row label="target_metric" value={experiment.target_metric} />
          <Row
            label="canary_drift_threshold"
            value={String(experiment.canary_drift_threshold)}
          />
        </dl>
      </details>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
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
