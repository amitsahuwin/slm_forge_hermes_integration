import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import CanaryDriftChart from '../components/ratchet/CanaryDriftChart';
import HyperparamHeatmap from '../components/ratchet/HyperparamHeatmap';
import IterationTable from '../components/ratchet/IterationTable';
import RatchetTimeline from '../components/ratchet/RatchetTimeline';
import { type Run, type SessionStatus, type TrainingSession, api } from '../lib/api';

const STATUS_STYLES: Record<SessionStatus, string> = {
  queued: 'text-zinc-400',
  running: 'text-emerald-400',
  completed: 'text-sky-400',
  failed: 'text-rose-400',
  cancelled: 'text-zinc-500',
};

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sid = id ? parseInt(id, 10) : undefined;
  const [session, setSession] = useState<TrainingSession | null>(null);
  const [iterations, setIterations] = useState<Run[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (sid === undefined) return;
    let alive = true;
    const tick = async () => {
      try {
        const [s, its] = await Promise.all([api.getSession(sid), api.listIterations(sid)]);
        if (alive) {
          setSession(s);
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

  if (error) return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!session) return <div className="text-sm text-zinc-500">Loading session #{id}…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">{session.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Session #{session.id} · {session.dataset} ·{' '}
            {session.base_model.replace(/^mlx-community\//, '')} · {session.method}
          </p>
        </div>
        <div className={`font-mono text-sm ${STATUS_STYLES[session.status]}`}>● {session.status}</div>
      </div>

      {session.error_message && (
        <div className="rounded-md bg-rose-950/40 px-3 py-2 font-mono text-xs text-rose-300">
          {session.error_message}
        </div>
      )}

      {/* Top stats */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="round" value={`${session.current_round + 1} / ${session.max_rounds}`} />
        <Stat
          label="best metric"
          value={session.best_metric_value !== null ? session.best_metric_value.toFixed(4) : '—'}
        />
        <Stat
          label="best run"
          value={
            session.best_run_id !== null ? (
              <Link to={`/runs/${session.best_run_id}`} className="text-emerald-400 hover:underline">
                #{session.best_run_id}
              </Link>
            ) : (
              '—'
            )
          }
        />
        <Stat label="accepted" value={`${iterations.filter((i) => i.was_accepted).length} / ${iterations.length}`} />
      </section>

      {/* Ratchet timeline (main graph) */}
      <RatchetTimeline iterations={iterations} targetMetric={session.target_metric} />

      {/* Heatmap + Canary drift side by side on wide screens */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <HyperparamHeatmap iterations={iterations} />
        <CanaryDriftChart iterations={iterations} threshold={session.canary_drift_threshold} />
      </div>

      {/* Iteration table */}
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
          Session configuration
        </summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-3">
          <Row label="base_model" value={session.base_model} />
          <Row label="method" value={session.method} />
          <Row label="iters" value={String(session.iters)} />
          <Row label="batch_size" value={String(session.batch_size)} />
          <Row label="learning_rate" value={session.learning_rate.toExponential(2)} />
          <Row label="num_layers" value={String(session.num_layers)} />
          <Row label="max_seq_length" value={String(session.max_seq_length)} />
          <Row label="max_rounds" value={String(session.max_rounds)} />
          <Row label="plateau_patience" value={String(session.plateau_patience)} />
          <Row label="min_delta" value={String(session.min_delta)} />
          <Row label="target_metric" value={session.target_metric} />
          <Row label="canary_drift_threshold" value={String(session.canary_drift_threshold)} />
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
