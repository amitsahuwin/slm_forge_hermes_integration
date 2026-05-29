/**
 * Main ratchet graph: X = iteration #, Y = primary metric.
 * Dots: green=accepted, red=rejected, yellow=errored.
 * Solid line connects only accepted points (the ratchet stairstep).
 */
import { useMemo } from 'react';
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[]; targetMetric: 'val_loss' | 'canary_loss' };

type Point = {
  iter: number;
  metric: number | null;
  accepted: number | null;     // for green scatter
  rejected: number | null;     // for red scatter
  errored: number | null;      // for yellow scatter
  ratchet: number | null;      // green-only line value (NaN-bridged)
  lr: number;
  batch_size: number;
  num_layers: number;
  iters: number;
  reasoning: string | null;
  status: string;
};

function metricOf(it: Run, m: 'val_loss' | 'canary_loss'): number | null {
  if (m === 'val_loss') return it.final_val_loss;
  return it.canary_loss;
}

export default function RatchetTimeline({ iterations, targetMetric }: Props) {
  const data: Point[] = useMemo(() => {
    const sorted = [...iterations].sort(
      (a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0),
    );
    return sorted.map((it) => {
      const v = metricOf(it, targetMetric);
      const errored = it.status === 'failed' || it.status === 'cancelled';
      return {
        iter: it.iteration_number ?? 0,
        metric: v,
        accepted: it.was_accepted === true && v !== null ? v : null,
        rejected: it.was_accepted === false && v !== null && !errored ? v : null,
        errored: errored ? v ?? 0 : null,
        ratchet: it.was_accepted === true ? v : null,
        lr: it.learning_rate,
        batch_size: it.batch_size,
        num_layers: it.num_layers,
        iters: it.iters,
        reasoning: it.mutation_reasoning,
        status: it.status,
      };
    });
  }, [iterations, targetMetric]);

  if (data.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/40 text-sm text-zinc-500">
        Ratchet graph appears once iteration 0 completes…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Ratchet timeline
        </h3>
        <div className="flex items-center gap-4 font-mono text-xs">
          <span className="text-zinc-500">Y: {targetMetric}</span>
          <Legend dot="emerald" label="accepted" />
          <Legend dot="rose" label="rejected" />
          <Legend dot="amber" label="error" />
        </div>
      </div>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis
              dataKey="iter"
              type="number"
              domain={[0, 'dataMax']}
              stroke="#71717a"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              label={{ value: 'iteration', position: 'insideBottom', offset: -5, fontSize: 11, fill: '#71717a' }}
            />
            <YAxis
              stroke="#71717a"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              domain={['auto', 'auto']}
            />
            <Tooltip content={<RatchetTooltip />} />
            <Line
              type="stepAfter"
              dataKey="ratchet"
              stroke="#34d399"
              strokeWidth={2.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Scatter dataKey="accepted" fill="#34d399" shape="circle" />
            <Scatter dataKey="rejected" fill="#fb7185" shape="cross" />
            <Scatter dataKey="errored" fill="#fbbf24" shape="triangle" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Legend({ dot, label }: { dot: string; label: string }) {
  const color =
    dot === 'emerald' ? 'bg-emerald-400' : dot === 'rose' ? 'bg-rose-400' : 'bg-amber-400';
  return (
    <span className="flex items-center gap-1.5 text-zinc-400">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function RatchetTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  const status =
    p.accepted !== null ? 'accepted' : p.rejected !== null ? 'rejected' : 'errored';
  return (
    <div className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-xs">
      <div className="text-zinc-100">iter #{p.iter} · {status}</div>
      <div className="mt-1 text-zinc-400">metric = {p.metric?.toFixed(4) ?? '—'}</div>
      <div className="text-zinc-500">lr={p.lr.toExponential(1)} · bs={p.batch_size} · layers={p.num_layers} · it={p.iters}</div>
      {p.reasoning && (
        <div className="mt-2 max-w-xs whitespace-normal text-zinc-300">"{p.reasoning}"</div>
      )}
    </div>
  );
}
