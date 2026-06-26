import { useMemo } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Metric } from '../../lib/api';

type Props = { metrics: Metric[] };

type Row = { step: number; train_loss?: number; val_loss?: number };

export default function LiveLossChart({ metrics }: Props) {
  const data: Row[] = useMemo(() => {
    const byStep = new Map<number, Row>();
    for (const m of metrics) {
      if (m.name !== 'train_loss' && m.name !== 'val_loss') continue;
      const row = byStep.get(m.step) ?? { step: m.step };
      if (m.name === 'train_loss') row.train_loss = m.value;
      else if (m.name === 'val_loss') row.val_loss = m.value;
      byStep.set(m.step, row);
    }
    return [...byStep.values()].sort((a, b) => a.step - b.step);
  }, [metrics]);

  if (data.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-lg border border-hcl-light-blue bg-white text-sm text-hcl-dark/50">
        Waiting for first metric…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-hcl-light-blue bg-white p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
        Live loss
      </h3>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid stroke="#DCE6F0" strokeDasharray="3 3" />
            <XAxis dataKey="step" stroke="#17707F" tick={{ fontSize: 11, fontFamily: 'monospace' }} />
            <YAxis
              stroke="#17707F"
              tick={{ fontSize: 11, fontFamily: 'monospace' }}
              domain={['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#F7F7FC',
                border: '1px solid #DCE6F0',
                fontSize: 12,
                fontFamily: 'monospace',
              }}
              labelStyle={{ color: '#17707F' }}
            />
            <Legend wrapperStyle={{ fontSize: 12, fontFamily: 'monospace' }} />
            <Line
              type="monotone"
              dataKey="train_loss"
              name="train"
              stroke="#2EC0CB"
              dot={false}
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="val_loss"
              name="val"
              stroke="#f59e0b"
              dot={{ r: 3 }}
              strokeWidth={2}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
