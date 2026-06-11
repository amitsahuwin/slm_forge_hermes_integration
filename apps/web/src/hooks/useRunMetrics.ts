import { useEffect, useRef, useState } from 'react';
import { withAuth } from '../auth/fetchInterceptor';
import { API_URL, type Metric, type RunStatus, api } from '../lib/api';

export type MetricsState = {
  metrics: Metric[];
  status: RunStatus | null;
  error: string | null;
};

/**
 * Hook: fetches initial metrics for a run, then subscribes to /stream (SSE).
 * Auto-closes the EventSource on terminal status (completed/failed/cancelled).
 */
export function useRunMetrics(runId: number | undefined) {
  const [state, setState] = useState<MetricsState>({
    metrics: [],
    status: null,
    error: null,
  });
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (runId === undefined) return;
    let cancelled = false;

    // 1. seed with whatever's already in the DB
    api
      .listMetrics(runId)
      .then((metrics) => {
        if (!cancelled) setState((s) => ({ ...s, metrics }));
      })
      .catch((e: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
      });

    // 2. subscribe to live updates
    const es = new EventSource(withAuth(`${API_URL}/api/v1/runs/${runId}/stream`));
    esRef.current = es;

    es.addEventListener('metric', (ev) => {
      const m = JSON.parse((ev as MessageEvent).data) as Metric;
      setState((s) => ({ ...s, metrics: [...s.metrics, m] }));
    });

    es.addEventListener('status', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as { status: RunStatus };
      setState((s) => ({ ...s, status: data.status }));
    });

    es.addEventListener('done', () => {
      es.close();
    });

    es.onerror = () => {
      // EventSource auto-retries; surface a soft hint only
      setState((s) => ({ ...s, error: s.error ?? 'stream reconnecting…' }));
    };

    return () => {
      cancelled = true;
      es.close();
      esRef.current = null;
    };
  }, [runId]);

  return state;
}
