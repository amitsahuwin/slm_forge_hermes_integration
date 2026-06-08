import { useCallback, useEffect, useState } from 'react';
import HermesStatusCard from '../components/HermesStatusCard';
import LogDrawer from '../components/LogDrawer';
import type { WorkerName } from '../components/LogPane';
import { API_URL } from '../lib/api';

type Health = {
  status: string;
  version: string;
  phase?: string;
  python: string;
  capabilities: Record<string, boolean>;
};

type HermesStatus = {
  ollama_reachable: boolean;
  worker_running: boolean;
  worker_last_seen: string | null;
};

type WorkerTone = 'ok' | 'warn' | 'down' | 'unknown';

type WorkerActivity = {
  tone: WorkerTone;
  label: string;
  lastLine: string;
};

type LogsResponse = {
  worker: string;
  path: string;
  exists: boolean | string;
  lines: string[];
};

const WORKER_META: Record<
  WorkerName,
  { title: string; subtitle: string; hint: string }
> = {
  api: {
    title: 'API',
    subtitle: 'FastAPI control plane',
    hint: 'make api',
  },
  trainer: {
    title: 'Trainer',
    subtitle: 'MLX-LM LoRA worker',
    hint: 'make trainer',
  },
  exporter: {
    title: 'Exporter',
    subtitle: 'GGUF fuse + quantize',
    hint: 'make exporter',
  },
  ratchet: {
    title: 'Ratchet',
    subtitle: 'Hermes autoresearch loop',
    hint: 'make ratchet',
  },
};

const TONE_PILL: Record<WorkerTone, string> = {
  ok: 'border-emerald-700/40 bg-emerald-900/30 text-emerald-300',
  warn: 'border-amber-700/40 bg-amber-900/30 text-amber-300',
  down: 'border-rose-700/40 bg-rose-900/30 text-rose-300',
  unknown: 'border-zinc-700/60 bg-zinc-800/40 text-zinc-400',
};

const TONE_DOT: Record<WorkerTone, string> = {
  ok: 'bg-emerald-400',
  warn: 'bg-amber-400',
  down: 'bg-rose-400',
  unknown: 'bg-zinc-500',
};

/**
 * Parse "HH:MM:SS" from the start of a log line and return seconds-since-now
 * relative to the local clock. Returns Infinity when no timestamp is found.
 * The trainer/exporter logs use Python's "%H:%M:%S" datefmt so the date is
 * implicit — we assume "today" and clamp negative deltas (just past midnight).
 */
function ageOfLogLineSeconds(line: string): number {
  const match = line.match(/^(\d{2}):(\d{2}):(\d{2})/);
  if (!match) return Number.POSITIVE_INFINITY;
  const [, hh, mm, ss] = match;
  const now = new Date();
  const lineDate = new Date(now);
  lineDate.setHours(Number(hh), Number(mm), Number(ss), 0);
  let delta = (now.getTime() - lineDate.getTime()) / 1000;
  // If the log appears to be in the future, the file likely rolled past
  // midnight — treat it as ~1s old to avoid a huge negative.
  if (delta < -60) delta += 24 * 3600;
  return Math.max(0, delta);
}

function deriveWorkerActivity(resp: LogsResponse | null, error: boolean): WorkerActivity {
  if (error) {
    return { tone: 'unknown', label: 'unreachable', lastLine: 'API unreachable' };
  }
  if (!resp) {
    return { tone: 'unknown', label: 'checking…', lastLine: '…' };
  }
  const exists =
    resp.exists === true || resp.exists === 'true';
  if (!exists) {
    return { tone: 'down', label: 'not started', lastLine: 'Worker not started' };
  }
  const lines = resp.lines ?? [];
  if (lines.length === 0) {
    return { tone: 'warn', label: 'idle', lastLine: 'Log file empty' };
  }
  const last = lines[lines.length - 1];
  const ageSec = ageOfLogLineSeconds(last);
  if (!Number.isFinite(ageSec)) {
    return { tone: 'warn', label: 'idle', lastLine: 'No timestamp on last line' };
  }
  if (ageSec < 30) {
    return {
      tone: 'ok',
      label: 'active',
      lastLine: `Last log line: ${Math.round(ageSec)}s ago`,
    };
  }
  if (ageSec < 3600) {
    return {
      tone: 'warn',
      label: 'idle',
      lastLine: `Last log line: ${Math.round(ageSec / 60)}m ago`,
    };
  }
  return {
    tone: 'warn',
    label: 'stale',
    lastLine: `Last log line: ${Math.round(ageSec / 3600)}h ago`,
  };
}

export default function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [hermes, setHermes] = useState<HermesStatus | null>(null);
  const [logs, setLogs] = useState<Record<WorkerName, LogsResponse | null>>({
    api: null,
    trainer: null,
    exporter: null,
    ratchet: null,
  });
  const [logsErr, setLogsErr] = useState<Record<WorkerName, boolean>>({
    api: false,
    trainer: false,
    exporter: false,
    ratchet: false,
  });
  const [openWorker, setOpenWorker] = useState<WorkerName | null>(null);
  // Tick once a second so "Xs ago" labels update between polls.
  const [, setTick] = useState(0);

  // /health + /hermes/status polling
  useEffect(() => {
    let cancelled = false;

    const loadHealth = async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/health`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data: Health = await r.json();
        if (!cancelled) {
          setHealth(data);
          setHealthError(null);
        }
      } catch (e) {
        if (!cancelled) setHealthError(e instanceof Error ? e.message : String(e));
      }
    };

    const loadHermes = async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/hermes/status`);
        if (!r.ok) return;
        const data: HermesStatus = await r.json();
        if (!cancelled) setHermes(data);
      } catch {
        /* non-fatal: the dedicated card surfaces detail */
      }
    };

    loadHealth();
    loadHermes();
    const id = window.setInterval(() => {
      loadHealth();
      loadHermes();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // Poll /logs/{worker} for trainer + exporter (api & ratchet derive elsewhere
  // but we still tail their files so the drawer is meaningful).
  useEffect(() => {
    let cancelled = false;
    const workers: WorkerName[] = ['api', 'trainer', 'exporter', 'ratchet'];

    const loadOne = async (w: WorkerName) => {
      try {
        const r = await fetch(`${API_URL}/api/v1/logs/${w}?n=20`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data: LogsResponse = await r.json();
        if (cancelled) return;
        setLogs((prev) => ({ ...prev, [w]: data }));
        setLogsErr((prev) => ({ ...prev, [w]: false }));
      } catch {
        if (cancelled) return;
        setLogsErr((prev) => ({ ...prev, [w]: true }));
      }
    };

    const loadAll = () => workers.forEach((w) => void loadOne(w));
    loadAll();
    const id = window.setInterval(loadAll, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  // 1s tick for relative-time labels.
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const apiOk = !healthError && health?.status === 'ok';

  const activityFor = useCallback(
    (worker: WorkerName): WorkerActivity => {
      if (worker === 'api') {
        if (healthError) {
          return { tone: 'down', label: 'down', lastLine: healthError };
        }
        if (!health) {
          return { tone: 'unknown', label: 'connecting', lastLine: 'Connecting…' };
        }
        return {
          tone: 'ok',
          label: 'ok',
          lastLine: `Responding · ${health.version}`,
        };
      }
      if (worker === 'ratchet' && hermes) {
        if (hermes.worker_running) {
          return {
            tone: 'ok',
            label: 'running',
            lastLine: hermes.worker_last_seen
              ? `Heartbeat ${relativeTime(hermes.worker_last_seen)}`
              : 'Heartbeat live',
          };
        }
        if (hermes.worker_last_seen) {
          return {
            tone: 'warn',
            label: 'idle',
            lastLine: `Last seen ${relativeTime(hermes.worker_last_seen)}`,
          };
        }
        // Fall through to log-based detection if hermes hasn't started yet.
      }
      return deriveWorkerActivity(logs[worker], logsErr[worker]);
    },
    [health, healthError, hermes, logs, logsErr],
  );

  const workers: WorkerName[] = ['api', 'trainer', 'exporter', 'ratchet'];

  return (
    <div className="space-y-8">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Local-first SLM fine-tuning lab · Hermes-driven autoresearch
          </p>
        </div>
        <HealthPill ok={apiOk} version={health?.version} error={healthError} />
      </div>

      {/* ── Capabilities ────────────────────────────────────────── */}
      {health && (
        <section>
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
            Capabilities
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-5">
            {Object.entries(health.capabilities).map(([key, enabled]) => (
              <div
                key={key}
                className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5"
              >
                <div className="font-mono text-xs text-zinc-500">{key}</div>
                <div
                  className={`mt-1 font-mono text-sm ${
                    enabled ? 'text-emerald-400' : 'text-zinc-600'
                  }`}
                >
                  {enabled ? '● enabled' : '○ pending'}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Service tiles ───────────────────────────────────────── */}
      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
          Services
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {workers.map((w) => (
            <ServiceTile
              key={w}
              worker={w}
              activity={activityFor(w)}
              onOpen={() => setOpenWorker(w)}
            />
          ))}
        </div>
      </section>

      {/* ── Hermes detail strip ─────────────────────────────────── */}
      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wider text-zinc-500">
          Hermes Agent
        </h2>
        <HermesStatusCard />
      </section>

      <LogDrawer worker={openWorker} onClose={() => setOpenWorker(null)} />
    </div>
  );
}

// ─── Sub-components ────────────────────────────────────────────────

function HealthPill({
  ok,
  version,
  error,
}: {
  ok: boolean;
  version: string | undefined;
  error: string | null;
}) {
  const tone: WorkerTone = error ? 'down' : ok ? 'ok' : 'unknown';
  const label = error ? 'down' : ok ? 'ok' : '…';
  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 ${TONE_PILL[tone]}`}
      title={error ?? undefined}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[tone]}`} aria-hidden />
      <span className="font-mono text-xs">● {label}</span>
      {version && (
        <span className="font-mono text-xs text-zinc-500">· v{version}</span>
      )}
    </div>
  );
}

function ServiceTile({
  worker,
  activity,
  onOpen,
}: {
  worker: WorkerName;
  activity: WorkerActivity;
  onOpen: () => void;
}) {
  const meta = WORKER_META[worker];
  const running = activity.tone === 'ok';
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex h-full flex-col rounded-xl border border-zinc-800 bg-zinc-900/40 p-5 text-left transition hover:border-zinc-700 hover:bg-zinc-900/60 focus:outline-none focus:ring-2 focus:ring-zinc-600"
      aria-label={`Open ${meta.title} log`}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-lg font-semibold tracking-tight text-zinc-100">
            {meta.title}
          </div>
          <div className="mt-0.5 text-xs text-zinc-500">{meta.subtitle}</div>
        </div>
        <StatusPill tone={activity.tone} label={activity.label} />
      </div>

      <div className="mt-auto space-y-2">
        <p className="font-mono text-xs text-zinc-400">{activity.lastLine}</p>
        {!running && (
          <code className="block rounded bg-zinc-800 px-2 py-1.5 font-mono text-xs text-zinc-200">
            {meta.hint}
          </code>
        )}
        <p className="text-xs text-zinc-600 group-hover:text-zinc-500">
          Click to tail log →
        </p>
      </div>
    </button>
  );
}

function StatusPill({ tone, label }: { tone: WorkerTone; label: string }) {
  return (
    <span
      className={`flex shrink-0 items-center gap-1.5 rounded-lg border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${TONE_PILL[tone]}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[tone]}`} aria-hidden />
      {label}
    </span>
  );
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}
