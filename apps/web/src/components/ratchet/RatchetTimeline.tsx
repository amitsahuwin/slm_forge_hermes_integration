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
      <div className="flex h-72 items-center justify-center rounded-lg border border-hcl-light-blue bg-white text-sm text-hcl-dark/50">
        Ratchet graph appears once iteration 0 completes…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-hcl-light-blue bg-white p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
          Ratchet timeline
        </h3>
        <div className="flex items-center gap-4 font-mono text-xs">
          <span className="text-hcl-dark/50">Y: {targetMetric}</span>
          <Legend dot="emerald" label="accepted" />
          <Legend dot="rose" label="rejected" />
          <Legend dot="amber" label="error" />
        </div>
      </div>
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#DCE6F0" strokeDasharray="3 3" />
            <XAxis
              dataKey="iter"
              type="number"
              domain={[0, 'dataMax']}
              stroke="#17707F"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              label={{ value: 'iteration', position: 'insideBottom', offset: -5, fontSize: 11, fill: '#17707F' }}
            />
            <YAxis
              stroke="#17707F"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              domain={['auto', 'auto']}
            />
            <Tooltip content={<RatchetTooltip />} />
            <Line
              type="stepAfter"
              dataKey="ratchet"
              stroke="#2EC0CB"
              strokeWidth={2.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
            <Scatter dataKey="accepted" fill="#2EC0CB" shape="circle" />
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
    dot === 'emerald' ? 'bg-hcl-teal' : dot === 'rose' ? 'bg-red-400' : 'bg-hcl-warning';
  return (
    <span className="flex items-center gap-1.5 text-hcl-dark/60">
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
    <div className="rounded-md border border-hcl-teal/30 bg-hcl-tech-grey px-3 py-2 font-mono text-xs">
      <div className="text-hcl-dark">iter #{p.iter} · {status}</div>
      <div className="mt-1 text-hcl-dark/60">metric = {p.metric?.toFixed(4) ?? '—'}</div>
      <div className="text-hcl-dark/50">lr={p.lr.toExponential(1)} · bs={p.batch_size} · layers={p.num_layers} · it={p.iters}</div>
      {p.reasoning && (
        <div className="mt-2 max-w-xs whitespace-normal text-hcl-dark/80">"{p.reasoning}"</div>
      )}
    </div>
  );
}
