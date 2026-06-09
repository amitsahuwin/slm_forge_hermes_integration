import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import HermesSkillButton, { type SkillResponse } from './HermesSkillButton';
import { API_URL } from '../lib/api';

type SynthesizeModalProps = {
  open: boolean;
  sourceDataset: string;
  sourceCount: number;
  onClose: () => void;
};

type ProgressEvent = { generated: number; target: number; batch: number };
type DoneEvent = {
  new_dataset: string;
  train: number;
  valid: number;
  canary: number;
  total: number;
  format?: string;
};

type Phase = 'configure' | 'running' | 'done' | 'error';

type StartResponse = { job_id: string; source_count: number; target_count: number };

const AUTO_CLOSE_MS = 6000;

export default function SynthesizeModal({
  open,
  sourceDataset,
  sourceCount,
  onClose,
}: SynthesizeModalProps) {
  const defaultName = useMemo(
    () => `${sourceDataset}-expanded-${Date.now().toString(36).slice(-4)}`,
    [sourceDataset],
  );

  const [newName, setNewName] = useState(defaultName);
  const [target, setTarget] = useState(100);
  const [style, setStyle] = useState('');
  const [trainR, setTrainR] = useState(0.8);
  const [validR, setValidR] = useState(0.15);
  const [canaryR, setCanaryR] = useState(0.05);

  const [phase, setPhase] = useState<Phase>('configure');
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [result, setResult] = useState<DoneEvent | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const autoCloseRef = useRef<number | null>(null);

  // Reset when re-opened.
  useEffect(() => {
    if (open) {
      setNewName(defaultName);
      setTarget(100);
      setStyle('');
      setTrainR(0.8);
      setValidR(0.15);
      setCanaryR(0.05);
      setPhase('configure');
      setProgress(null);
      setResult(null);
      setErrorMsg(null);
    }
  }, [open, defaultName]);

  // Cleanup on unmount / close.
  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
      if (autoCloseRef.current !== null) {
        window.clearTimeout(autoCloseRef.current);
      }
    };
  }, []);

  // Esc-to-close (only when not actively generating).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && phase !== 'running') {
        handleClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phase]);

  function handleClose() {
    esRef.current?.close();
    esRef.current = null;
    if (autoCloseRef.current !== null) {
      window.clearTimeout(autoCloseRef.current);
      autoCloseRef.current = null;
    }
    onClose();
  }

  function handleBackdrop(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget && phase !== 'running') {
      handleClose();
    }
  }

  const ratioSum = trainR + validR + canaryR;
  const ratiosValid = Math.abs(ratioSum - 1.0) <= 0.01;
  const trainRows = Math.max(1, Math.round(target * trainR));
  const validRows = Math.max(4, Math.round(target * validR));
  const canaryRows = Math.max(1, Math.round(target * canaryR));
  const previewTotal = trainRows + validRows + canaryRows;

  const canSubmit =
    phase === 'configure' &&
    newName.trim().length > 0 &&
    target >= 8 &&
    target <= 2000 &&
    ratiosValid &&
    validR * target >= 4;

  async function handleSubmit() {
    if (!canSubmit) return;
    setPhase('running');
    setProgress({ generated: 0, target, batch: 0 });
    setErrorMsg(null);

    try {
      const r = await fetch(`${API_URL}/api/v1/synth/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_dataset: sourceDataset,
          new_dataset: newName.trim(),
          target_count: target,
          style_guidance: style,
          train_ratio: trainR,
          valid_ratio: validR,
          canary_ratio: canaryR,
        }),
      });
      if (!r.ok) {
        let detail = '';
        try {
          const j = (await r.json()) as { detail?: string };
          detail = j.detail ?? '';
        } catch {
          /* ignore */
        }
        throw new Error(`HTTP ${r.status}${detail ? ` — ${detail}` : ''}`);
      }
      const start = (await r.json()) as StartResponse;
      attachStream(start.job_id);
    } catch (e) {
      setPhase('error');
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  }

  function attachStream(jobId: string) {
    const es = new EventSource(`${API_URL}/api/v1/synth/jobs/${jobId}/stream`);
    esRef.current = es;

    es.addEventListener('progress', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as ProgressEvent;
        setProgress(data);
      } catch {
        /* ignore malformed frame */
      }
    });

    es.addEventListener('done', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data) as DoneEvent;
        setResult(data);
        setPhase('done');
      } catch {
        setPhase('done');
      }
      es.close();
      esRef.current = null;
      // Auto-close after a few seconds so the user sees the summary.
      autoCloseRef.current = window.setTimeout(() => {
        handleClose();
      }, AUTO_CLOSE_MS);
    });

    es.addEventListener('error', (ev) => {
      // Two error sources: server-emitted `error` event with payload, OR
      // the EventSource emitting a transport `error` (no data).
      const msg = (ev as MessageEvent).data;
      if (typeof msg === 'string' && msg) {
        try {
          const data = JSON.parse(msg) as { message?: string };
          setErrorMsg(data.message ?? msg);
        } catch {
          setErrorMsg(msg);
        }
      } else if (es.readyState === EventSource.CLOSED) {
        setErrorMsg('Stream closed unexpectedly. The job may still be running — check Jobs tab.');
      } else {
        // Transient — let it retry; only flip to error if we never recover.
        return;
      }
      setPhase('error');
      es.close();
      esRef.current = null;
    });
  }

  if (!open) return null;

  const pctRaw = progress && progress.target > 0 ? (progress.generated / progress.target) * 100 : 0;
  const pct = Math.min(100, Math.max(0, pctRaw));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={handleBackdrop}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Synthesize dataset"
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Synthesize dataset</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Expand <span className="font-mono text-zinc-300">{sourceDataset}</span> (
              {sourceCount.toLocaleString()} seeds) via local Ollama.
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={phase === 'running'}
            className="rounded-md p-1 text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label="Close"
          >
            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M4.7 4.7a1 1 0 011.4 0L10 8.6l3.9-3.9a1 1 0 111.4 1.4L11.4 10l3.9 3.9a1 1 0 11-1.4 1.4L10 11.4l-3.9 3.9a1 1 0 11-1.4-1.4L8.6 10 4.7 6.1a1 1 0 010-1.4z" />
            </svg>
          </button>
        </div>

        {phase === 'configure' && (
          <div className="space-y-4">
            <Field label="New dataset name">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder-zinc-600 focus:border-emerald-600 focus:outline-none"
                placeholder="my-dataset-expanded"
              />
            </Field>

            <Field
              label="Target count"
              hint={`min 8, max 2000 — will produce ${previewTotal} rows after clamping`}
            >
              <input
                type="number"
                min={8}
                max={2000}
                value={target}
                onChange={(e) => setTarget(Number.parseInt(e.target.value || '0', 10))}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 focus:border-emerald-600 focus:outline-none"
              />
            </Field>

            <Field label="Style guidance (optional)">
              <textarea
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-emerald-600 focus:outline-none"
                placeholder="e.g. Make examples more diverse in technical depth"
              />
              <div className="mt-1">
                <HermesSkillButton
                  path={`/api/v1/hermes/synth-style/${encodeURIComponent(sourceDataset)}`}
                  label="Auto-fill from existing rows"
                  emoji="✨"
                  tone="zinc"
                  size="sm"
                  onResult={(r: SkillResponse) => {
                    const guidance = (r.parsed as { style_guidance?: string } | null)
                      ?.style_guidance;
                    if (guidance) setStyle(guidance);
                  }}
                />
              </div>
            </Field>

            <div>
              <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                Split ratios
              </div>
              <div className="space-y-2">
                <RatioSlider
                  label="train"
                  value={trainR}
                  onChange={setTrainR}
                  rows={trainRows}
                  accent="text-emerald-400"
                />
                <RatioSlider
                  label="valid"
                  value={validR}
                  onChange={setValidR}
                  rows={validRows}
                  accent="text-sky-400"
                />
                <RatioSlider
                  label="canary"
                  value={canaryR}
                  onChange={setCanaryR}
                  rows={canaryRows}
                  accent="text-amber-400"
                />
              </div>
              {!ratiosValid && (
                <div className="mt-2 font-mono text-[10px] text-rose-400">
                  ratios sum = {ratioSum.toFixed(3)} (must equal 1.00)
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-900"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Generate
              </button>
            </div>
          </div>
        )}

        {phase === 'running' && (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <Spinner />
              <div className="font-mono text-xs text-zinc-400">
                Generating batch {progress?.batch ?? 0} via local Ollama…
              </div>
            </div>
            <div>
              <div className="mb-1 flex items-baseline justify-between font-mono text-xs">
                <span className="text-zinc-400">
                  {progress?.generated ?? 0} / {progress?.target ?? target}
                </span>
                <span className="text-zinc-500">{pct.toFixed(0)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-zinc-900">
                <div
                  className="h-full bg-emerald-500 transition-[width] duration-300"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
            <p className="font-mono text-[10px] text-zinc-600">
              Close disabled while generating. This can take a few minutes for 100+ records.
            </p>
          </div>
        )}

        {phase === 'done' && result && (
          <div className="space-y-3">
            <div className="rounded-md border border-emerald-900/60 bg-emerald-950/30 p-3 text-sm text-emerald-200">
              Wrote <span className="font-mono">{result.new_dataset}</span> — {result.train} train ·{' '}
              {result.valid} valid · {result.canary} canary ({result.total} total).
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-900"
              >
                Close
              </button>
              <Link
                to={`/datasets/${encodeURIComponent(result.new_dataset)}`}
                onClick={handleClose}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
              >
                Open dataset →
              </Link>
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div className="space-y-3">
            <div className="rounded-md border border-rose-900/60 bg-rose-950/40 p-3 text-sm text-rose-200">
              {errorMsg ?? 'Synthesis failed.'}
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setPhase('configure')}
                className="rounded-md border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-900"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={handleClose}
                className="rounded-md bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      {children}
      {hint && <div className="mt-1 font-mono text-[10px] text-zinc-600">{hint}</div>}
    </label>
  );
}

function RatioSlider({
  label,
  value,
  onChange,
  rows,
  accent,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  rows: number;
  accent: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className={`w-14 font-mono text-xs ${accent}`}>{label}</div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(Number.parseFloat(e.target.value))}
        className="flex-1 accent-emerald-500"
      />
      <div className="w-24 text-right font-mono text-[11px] tabular-nums text-zinc-400">
        {(value * 100).toFixed(0)}% · {rows} rows
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin text-emerald-400" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}
