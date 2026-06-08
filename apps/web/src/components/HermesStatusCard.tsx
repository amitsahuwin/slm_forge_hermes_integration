import { useEffect, useState } from 'react';
import { API_URL } from '../lib/api';

type HermesStatus = {
  ollama_reachable: boolean;
  model: string;
  model_available: boolean;
  message: string;
  worker_running: boolean;
  worker_last_seen: string | null;
  skills_dir: string;
  skills_installed: string[];
};

type PillTone = 'ok' | 'warn' | 'down' | 'unknown';

const TONE_CLASSES: Record<PillTone, string> = {
  ok: 'border-emerald-700/40 bg-emerald-900/30 text-emerald-300',
  warn: 'border-amber-700/40 bg-amber-900/30 text-amber-300',
  down: 'border-rose-700/40 bg-rose-900/30 text-rose-300',
  unknown: 'border-zinc-700/60 bg-zinc-800/40 text-zinc-400',
};

const TONE_DOT: Record<PillTone, string> = {
  ok: 'bg-emerald-400',
  warn: 'bg-amber-400',
  down: 'bg-rose-400',
  unknown: 'bg-zinc-500',
};

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export default function HermesStatusCard() {
  const [status, setStatus] = useState<HermesStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Tick once a second so the "Xs ago" label updates between 5s polls.
  const [, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/hermes/status`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data: HermesStatus = await r.json();
        if (!cancelled) {
          setStatus(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };

    load();
    const pollId = window.setInterval(load, 5000);
    const tickId = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(pollId);
      window.clearInterval(tickId);
    };
  }, []);

  // ── Compute pill tones ──────────────────────────────────────────────
  let ollamaTone: PillTone = 'unknown';
  let modelTone: PillTone = 'unknown';
  let workerTone: PillTone = 'unknown';
  let skillsTone: PillTone = 'unknown';

  if (status) {
    ollamaTone = status.ollama_reachable ? 'ok' : 'down';
    modelTone = status.model_available ? 'ok' : status.ollama_reachable ? 'warn' : 'down';
    if (status.worker_running) {
      workerTone = 'ok';
    } else if (status.worker_last_seen) {
      // Was alive at some point — idle, not catastrophic.
      workerTone = 'warn';
    } else {
      workerTone = 'down';
    }
    skillsTone = status.skills_installed.length > 0 ? 'ok' : 'down';
  }

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
          Hermes Agent
        </h3>
        {status && (
          <span className="font-mono text-[10px] text-zinc-600">
            {status.worker_last_seen ? `worker ${relativeTime(status.worker_last_seen)}` : 'no worker yet'}
          </span>
        )}
      </div>

      {error && !status ? (
        <div className="font-mono text-sm text-rose-400">{error}</div>
      ) : !status ? (
        <span className="text-sm text-zinc-500">Connecting…</span>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Pill tone={ollamaTone} label="Ollama" value={status.ollama_reachable ? 'reachable' : 'down'} />
            <Pill
              tone={modelTone}
              label="Model"
              value={
                status.model_available
                  ? status.model
                  : status.ollama_reachable
                  ? 'not pulled'
                  : '—'
              }
            />
            <Pill
              tone={workerTone}
              label="Worker"
              value={
                status.worker_running
                  ? 'running'
                  : status.worker_last_seen
                  ? 'idle'
                  : 'not started'
              }
            />
            <Pill
              tone={skillsTone}
              label="Skills"
              value={`${status.skills_installed.length} installed`}
            />
          </div>

          <div className="mt-3 space-y-2">
            {(!status.ollama_reachable || !status.model_available) && (
              <Hint cmd="make install-hermes" reason={status.message || 'Ollama / model setup needed'} />
            )}
            {status.ollama_reachable && status.model_available && !status.worker_running && (
              <Hint cmd="make ratchet" reason="Ratchet worker is not heartbeating." />
            )}
            {status.skills_installed.length === 0 && (
              <Hint cmd="make hermes-install-skills" reason="No skills mirrored to the skills dir." />
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Pill({ tone, label, value }: { tone: PillTone; label: string; value: string }) {
  return (
    <div className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 ${TONE_CLASSES[tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[tone]}`} aria-hidden />
      <div className="min-w-0">
        <div className="font-mono text-[10px] uppercase tracking-wider opacity-60">{label}</div>
        <div className="truncate font-mono text-xs">{value}</div>
      </div>
    </div>
  );
}

function Hint({ cmd, reason }: { cmd: string; reason: string }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{reason}</p>
      <code className="mt-1 block rounded bg-zinc-800 px-2 py-1.5 font-mono text-xs text-zinc-200">
        {cmd}
      </code>
    </div>
  );
}
