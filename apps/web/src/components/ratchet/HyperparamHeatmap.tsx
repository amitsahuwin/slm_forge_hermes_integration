import { useMemo } from 'react';
import type { Run } from '../../lib/api';

type Props = { iterations: Run[] };

const PARAMS: { key: keyof Run; label: string; format: (v: number) => string }[] = [
  { key: 'learning_rate', label: 'lr', format: (v) => v.toExponential(1) },
  { key: 'batch_size', label: 'batch', format: (v) => v.toString() },
  { key: 'num_layers', label: 'layers', format: (v) => v.toString() },
  { key: 'iters', label: 'iters', format: (v) => v.toString() },
  { key: 'max_seq_length', label: 'seq_len', format: (v) => v.toString() },
];

function colorForChange(prev: number | undefined, curr: number): string {
  if (prev === undefined || prev === curr) return '#DCE6F0'; // hcl-light-blue (unchanged)
  const ratio = curr / prev;
  if (ratio > 1) {
    // increased — blue intensity
    const alpha = Math.min(1, Math.log(ratio) / Math.log(4));
    return `rgba(96, 165, 250, ${0.2 + 0.8 * alpha})`;
  } else {
    // decreased — red intensity
    const alpha = Math.min(1, -Math.log(ratio) / Math.log(4));
    return `rgba(251, 113, 133, ${0.2 + 0.8 * alpha})`;
  }
}

export default function HyperparamHeatmap({ iterations }: Props) {
  const sorted = useMemo(
    () => [...iterations].sort((a, b) => (a.iteration_number ?? 0) - (b.iteration_number ?? 0)),
    [iterations],
  );

  if (sorted.length === 0) {
    return null;
  }

  const cellW = 64;
  const cellH = 32;
  const labelW = 80;
  const headerH = 24;
  const width = labelW + sorted.length * cellW;
  const height = headerH + PARAMS.length * cellH;

  return (
    <div className="rounded-lg border border-hcl-light-blue bg-white p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
        Hyperparameter changes per iteration
      </h3>
      <div className="overflow-x-auto">
        <svg width={width} height={height} className="font-mono">
          {/* Header: iter numbers */}
          {sorted.map((it, i) => (
            <text
              key={`hdr-${i}`}
              x={labelW + i * cellW + cellW / 2}
              y={headerH - 6}
              textAnchor="middle"
              fontSize="11"
              fill="#17707F"
            >
              #{it.iteration_number ?? i}
            </text>
          ))}

          {/* Rows */}
          {PARAMS.map((param, rowIdx) => (
            <g key={param.key as string} transform={`translate(0, ${headerH + rowIdx * cellH})`}>
              <text x={labelW - 8} y={cellH / 2 + 4} textAnchor="end" fontSize="11" fill="#17707F">
                {param.label}
              </text>
              {sorted.map((it, colIdx) => {
                const curr = it[param.key] as number;
                const prev = colIdx > 0 ? (sorted[colIdx - 1][param.key] as number) : undefined;
                const fill = colorForChange(prev, curr);
                return (
                  <g key={colIdx} transform={`translate(${labelW + colIdx * cellW}, 0)`}>
                    <rect width={cellW - 2} height={cellH - 2} fill={fill} stroke="#F7F7FC" />
                    <text
                      x={cellW / 2 - 1}
                      y={cellH / 2 + 4}
                      textAnchor="middle"
                      fontSize="10"
                      fill="#14142B"
                    >
                      {param.format(curr)}
                    </text>
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
      </div>
      <div className="mt-2 flex items-center gap-4 font-mono text-xs text-hcl-dark/50">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: 'rgba(96, 165, 250, 0.7)' }} />
          increased
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: 'rgba(251, 113, 133, 0.7)' }} />
          decreased
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-hcl-tech-grey" />
          unchanged
        </span>
      </div>
    </div>
  );
}
