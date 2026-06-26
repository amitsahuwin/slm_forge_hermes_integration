import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { withAuth } from '../auth/fetchInterceptor';
import { API_URL } from '../lib/api';

type LogLine = {
  line: string;
  ts: string;
};

export type WorkerName = 'api' | 'trainer' | 'exporter' | 'ratchet';

type LogPaneProps = {
  /** Tail runs/<id>/training.log */
  runId?: number;
  /** Tail runs/_<worker>.log (api | trainer | exporter | ratchet) */
  worker?: WorkerName;
  /** Backward-compat: ratchet={true} is equivalent to worker="ratchet" */
  ratchet?: boolean;
  height?: string;
};

const MAX_LINES = 2000;

function classifyLine(line: string): string {
  if (/error|fail|✗/i.test(line)) return 'text-red-600';
  if (/warn/i.test(line)) return 'text-hcl-warning';
  if (line.includes('Iter ')) return 'text-hcl-dark-teal';
  return 'text-hcl-dark/80';
}

/**
 * Live tailing log viewer.
 *
 * Either pass ``runId`` to follow ``runs/<id>/training.log`` or pass
 * ``ratchet={true}`` to follow the ratchet worker log. Mirrors the SSE
 * pattern used by ``useRunMetrics``.
 */
export default function LogPane({ runId, worker, ratchet, height = '24rem' }: LogPaneProps) {
  // Normalize backward-compat `ratchet` flag into the unified worker form.
  const effectiveWorker: WorkerName | undefined = worker ?? (ratchet ? 'ratchet' : undefined);

  const [lines, setLines] = useState<LogLine[]>([]);
  const [paused, setPaused] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [hovering, setHovering] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const esRef = useRef<EventSource | null>(null);
  // Tracks whether the user has scrolled away from the bottom.
  const stuckToBottomRef = useRef(true);

  const streamUrl = useMemo(() => {
    if (effectiveWorker) return `${API_URL}/api/v1/logs/${effectiveWorker}/stream`;
    if (runId !== undefined) return `${API_URL}/api/v1/runs/${runId}/logs/stream`;
    return null;
  }, [runId, effectiveWorker]);

  const initialUrl = useMemo(() => {
    if (effectiveWorker) return `${API_URL}/api/v1/logs/${effectiveWorker}?n=500`;
    if (runId !== undefined) return `${API_URL}/api/v1/runs/${runId}/logs?n=500`;
    return null;
  }, [runId, effectiveWorker]);

  // 1. Initial paint via non-streaming endpoint.
  useEffect(() => {
    if (!initialUrl) return;
    let cancelled = false;
    // Reset state when switching log sources.
    setLines([]);
    setStreamError(null);
    setStatusMsg(null);
    fetch(initialUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { lines: string[] }) => {
        if (cancelled) return;
        const ts = new Date().toISOString();
        setLines(data.lines.map((line) => ({ line, ts })));
      })
      .catch(() => {
        // Non-fatal — the SSE stream may still produce content.
      });
    return () => {
      cancelled = true;
    };
  }, [initialUrl]);

  // 2. Live SSE stream.
  useEffect(() => {
    if (!streamUrl) return;
    const es = new EventSource(withAuth(streamUrl));
    esRef.current = es;

    es.addEventListener('log', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as LogLine;
      setLines((prev) => {
        const next = prev.length >= MAX_LINES ? prev.slice(prev.length - MAX_LINES + 1) : prev;
        return [...next, data];
      });
    });

    es.addEventListener('done', () => {
      es.close();
    });

    es.addEventListener('info', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data ?? '{}') as { message?: string };
        if (data.message) setStatusMsg(data.message);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('status', (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data ?? '{}') as {
          path?: string;
          exists?: boolean;
        };
        if (data.exists === false) {
          setStatusMsg(`waiting for ${data.path?.split('/').pop()} to be created…`);
        } else {
          setStatusMsg(null);
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('error', (ev) => {
      // SSE custom `error` event from the server (file-missing, etc.)
      try {
        const data = JSON.parse((ev as MessageEvent).data ?? '{}') as { message?: string };
        if (data.message) setStreamError(data.message);
      } catch {
        /* ignore parse errors from native onerror events */
      }
    });

    es.onerror = () => {
      setStreamError((e) => e ?? 'stream reconnecting…');
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [streamUrl]);

  // 3. Auto-scroll, but pause when user hovers or has scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (paused || hovering) return;
    if (!stuckToBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, paused, hovering]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stuckToBottomRef.current = distanceFromBottom < 24;
  }, []);

  const copyAll = useCallback(() => {
    const text = lines.map((l) => l.line).join('\n');
    void navigator.clipboard?.writeText(text);
  }, [lines]);

  const downloadLog = useCallback(() => {
    const text = lines.map((l) => l.line).join('\n');
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = effectiveWorker
      ? `${effectiveWorker}.log`
      : `run-${runId ?? 'unknown'}.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [lines, effectiveWorker, runId]);

  const label = effectiveWorker
    ? `${effectiveWorker}.log`
    : runId !== undefined
    ? `run #${runId} · training.log`
    : 'logs';

  return (
    <div className="rounded-lg border border-hcl-light-blue bg-hcl-tech-grey">
      <div className="flex items-center justify-between gap-2 border-b border-hcl-light-blue px-3 py-2">
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-hcl-dark">{label}</span>
          <span className="text-xs text-hcl-dark/50">{lines.length} lines</span>
          {streamError && (
            <span className="text-xs text-hcl-warning" title={streamError}>
              {streamError}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            className="rounded border border-hcl-light-blue px-2 py-0.5 text-xs text-hcl-dark-teal hover:bg-hcl-light-blue"
          >
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            type="button"
            onClick={copyAll}
            className="rounded border border-hcl-light-blue px-2 py-0.5 text-xs text-hcl-dark-teal hover:bg-hcl-light-blue"
          >
            Copy all
          </button>
          <button
            type="button"
            onClick={downloadLog}
            className="rounded border border-hcl-light-blue px-2 py-0.5 text-xs text-hcl-dark-teal hover:bg-hcl-light-blue"
          >
            Download .log
          </button>
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
        className="overflow-auto px-3 py-2 font-mono text-xs leading-relaxed"
        style={{ height }}
      >
        {lines.length === 0 ? (
          <div className="text-hcl-dark/40">
            {statusMsg ?? 'Waiting for log output…'}
          </div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={`whitespace-pre-wrap ${classifyLine(l.line)}`}>
              {l.line || ' '}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
