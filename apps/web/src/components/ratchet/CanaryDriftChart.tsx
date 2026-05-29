import { useMemo } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[]; threshold: number };

export default function CanaryDriftChart({ iterations, threshold }: Props) {
  const data = useMemo(() => {
    return [...iterations]
      .sort((a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0))
      .map((it) => ({
        iter: it.iteration_number ?? 0,
        drift:
          it.final_val_loss !== null && it.canary_loss !== null
            ? Math.abs(it.canary_loss - it.final_val_loss)
            : null,
      }));
  }, [iterations]);

  const hasAny = data.some((d) => d.drift !== null);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Canary drift (Goodhart guardrail)
        </h3>
        <span className="font-mono text-xs text-zinc-500">threshold: {threshold.toFixed(2)}</span>
      </div>
      {!hasAny ? (
        <div className="flex h-32 items-center justify-center text-sm text-zinc-500">
          Canary eval not yet wired into the trainer (Phase 2.5).
        </div>
      ) : (
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="iter" stroke="#71717a" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
              <YAxis stroke="#71717a" tick={{ fontSize: 11, fontFamily: 'monospace' }} domain={[0, 'auto']} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#18181b',
                  border: '1px solid #3f3f46',
                  fontSize: 12,
                  fontFamily: 'monospace',
                }}
              />
              <ReferenceLine y={threshold} stroke="#f43f5e" strokeDasharray="4 4" />
              <Line
                type="monotone"
                dataKey="drift"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={{ r: 3 }}
                connectNulls
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
