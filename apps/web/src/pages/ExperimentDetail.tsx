import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import CanaryDriftChart from '../components/ratchet/CanaryDriftChart';
import HyperparamHeatmap from '../components/ratchet/HyperparamHeatmap';
import IterationTable from '../components/ratchet/IterationTable';
import RatchetTimeline from '../components/ratchet/RatchetTimeline';
import {
  type Run,
  type SessionStatus,
  type TrainingSession as Experiment,
  api,
} from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function ExperimentDetail() {
  const { id } = useParams<{ id: string }>();
  const sid = id ? parseInt(id, 10) : undefined;
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [iterations, setIterations] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

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
    return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!experiment) return <div className="text-sm text-zinc-500">Loading experiment #{id}…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{experiment.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Experiment #{experiment.id} · {experiment.dataset} ·{' '}
            {experiment.base_model.replace(/^mlx-community\//, '')} · {experiment.method}
          </p>
        </div>
        <div className={`font-mono text-sm ${STATUS_STYLES[experiment.status]}`}>
          ● {experiment.status}
        </div>
      </div>

      {experiment.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
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
                className="text-emerald-400 hover:underline"
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
        <CanaryDriftChart
          iterations={iterations}
          threshold={experiment.canary_drift_threshold}
        />
      </div>

      <section className="space-y-3">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Iterations</h3>
        {iterations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-8 text-center text-sm text-zinc-500">
            Waiting for ratchet worker to create the first iteration.
          </div>
        ) : (
          <IterationTable iterations={iterations} />
        )}
      </section>

      <details className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-zinc-500">
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
