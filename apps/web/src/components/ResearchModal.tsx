import { useEffect, useRef, useState } from 'react';
import { withAuth } from '../auth/fetchInterceptor';
import { API_URL } from '../lib/api';

export type Depth = 'quick' | 'standard' | 'deep';

type ResearchModalProps = {
  open: boolean;
  onClose: () => void;
  onDone: (result: DoneEvent) => void;
};

type ProgressEvent = {
  stage: string;
  title?: string;
  index?: number;
  total?: number;
};

type DoneEvent = {
  filename: string;
  path: string;
  bytes: number;
};

type Phase = 'configure' | 'running' | 'done' | 'error';

type StartResponse = { job_id: string };

const AUTO_CLOSE_MS = 2500;

const DEPTH_HINTS: Record<Depth, string> = {
  quick: '5-7 sections, ~200 words each. Fastest — ~1-2 min on a small model.',
  standard: '5-7 sections, ~400 words each. Balanced — ~3-5 min.',
  deep: '5-7 sections, ~600 words + self-critique pass. Slowest — ~6-10 min.',
};

export default function ResearchModal({ open, onClose, onDone }: ResearchModalProps) {
  const [topic, setTopic] = useState('');
  const [depth, setDepth] = useState<Depth>('standard');
  const [phase, setPhase] = useState<Phase>('configure');
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [result, setResult] = useState<DoneEvent | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const autoCloseRef = useRef<number | null>(null);

  // Reset state on (re)open.
  useEffect(() => {
    if (open) {
      setTopic('');
      setDepth('standard');
      setPhase('configure');
      setProgress(null);
      setResult(null);
      setErrorMsg(null);
    }
  }, [open]);

  // Cleanup any lingering stream / timer on unmount.
  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
      if (autoCloseRef.current !== null) {
        window.clearTimeout(autoCloseRef.current);
      }
    };
  }, []);

  // Esc to close — blocked while generating.
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

  const canSubmit = phase === 'configure' && topic.trim().length >= 3;

  async function handleSubmit() {
    if (!canSubmit) return;
    setPhase('running');
    setProgress({ stage: 'starting' });
    setErrorMsg(null);

    try {
      const r = await fetch(`${API_URL}/api/v1/research/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: topic.trim(), depth }),
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
    const es = new EventSource(withAuth(`${API_URL}/api/v1/research/jobs/${jobId}/stream`));
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
        onDone(data);
      } catch {
        setPhase('done');
      }
      es.close();
      esRef.current = null;
      autoCloseRef.current = window.setTimeout(() => {
        handleClose();
      }, AUTO_CLOSE_MS);
    });

    es.addEventListener('error', (ev) => {
      const msg = (ev as MessageEvent).data;
      if (typeof msg === 'string' && msg) {
        try {
          const data = JSON.parse(msg) as { message?: string };
          setErrorMsg(data.message ?? msg);
        } catch {
          setErrorMsg(msg);
        }
      } else if (es.readyState === EventSource.CLOSED) {
        setErrorMsg('Stream closed unexpectedly. The job may still be running.');
      } else {
        // Transient — let EventSource retry.
        return;
      }
      setPhase('error');
      es.close();
      esRef.current = null;
    });
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={handleBackdrop}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Generate market research report"
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Market research</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Generate a structured markdown report via local Ollama.
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
            <Field label="Topic">
              <textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                rows={3}
                autoFocus
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-emerald-600 focus:outline-none"
                placeholder="e.g. On-device SLM fine-tuning vs hosted fine-tuning APIs"
              />
            </Field>

            <div>
              <div className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                Depth
              </div>
              <div className="space-y-1.5">
                {(['quick', 'standard', 'deep'] as Depth[]).map((d) => (
                  <label
                    key={d}
                    className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 transition-colors ${
                      depth === d
                        ? 'border-emerald-700 bg-emerald-950/30'
                        : 'border-zinc-800 bg-zinc-900/40 hover:border-zinc-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="depth"
                      value={d}
                      checked={depth === d}
                      onChange={() => setDepth(d)}
                      className="mt-1 accent-emerald-500"
                    />
                    <div className="min-w-0">
                      <div className="font-mono text-xs capitalize text-zinc-200">{d}</div>
                      <div className="mt-0.5 font-mono text-[10px] text-zinc-500">
                        {DEPTH_HINTS[d]}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
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

        {phase === 'running' && <RunningView progress={progress} />}

        {phase === 'done' && result && (
          <div className="space-y-3">
            <div className="rounded-md border border-emerald-900/60 bg-emerald-950/30 p-3 text-sm text-emerald-200">
              Saved <span className="font-mono">{result.filename}</span> ({fmtBytes(result.bytes)}).
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
              >
                View report
              </button>
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div className="space-y-3">
            <div className="rounded-md border border-rose-900/60 bg-rose-950/40 p-3 text-sm text-rose-200">
              {errorMsg ?? 'Research run failed.'}
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

function RunningView({ progress }: { progress: ProgressEvent | null }) {
  const stage = progress?.stage ?? 'starting';
  const idx = progress?.index ?? 0;
  const total = progress?.total ?? 0;
  const pct = total > 0 ? Math.min(100, Math.max(0, (idx / total) * 100)) : 0;

  const stageLabel = STAGE_LABELS[stage] ?? stage;

  // Show a per-section progress bar once we're past outline.
  const showBar =
    total > 0 && (stage === 'section_start' || stage === 'section_done');

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Spinner />
        <div className="font-mono text-xs text-zinc-300">{stageLabel}</div>
      </div>

      {progress?.title && (
        <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-2 font-mono text-xs text-zinc-400">
          <span className="text-zinc-600">section: </span>
          <span className="text-zinc-200">{progress.title}</span>
          {total > 0 && (
            <span className="ml-2 text-zinc-600">
              ({Math.max(idx, 1)} / {total})
            </span>
          )}
        </div>
      )}

      {showBar && (
        <div>
          <div className="mb-1 flex items-baseline justify-between font-mono text-xs">
            <span className="text-zinc-400">section progress</span>
            <span className="text-zinc-500">{pct.toFixed(0)}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-zinc-900">
            <div
              className="h-full bg-emerald-500 transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      <p className="font-mono text-[10px] text-zinc-600">
        Close disabled while generating. Deep reports can take 6-10 minutes on small models.
      </p>
    </div>
  );
}

const STAGE_LABELS: Record<string, string> = {
  starting: 'Starting research run…',
  outline: 'Building section outline…',
  section_start: 'Writing section…',
  section_done: 'Section complete.',
  critique: 'Self-critique pass — looking for weak claims…',
  comparison: 'Building comparison table…',
  compose: 'Composing final markdown…',
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      {children}
    </label>
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

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
