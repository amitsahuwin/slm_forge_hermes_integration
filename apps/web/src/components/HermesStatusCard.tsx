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
  // Skills viewer modal
  const [skillsOpen, setSkillsOpen] = useState(false);

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
            <button
              type="button"
              onClick={() => setSkillsOpen(true)}
              className="block text-left"
              title="Click to view installed skills"
            >
              <Pill
                tone={skillsTone}
                label="Skills"
                value={`${status.skills_installed.length} installed →`}
              />
            </button>
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

      {skillsOpen && (
        <SkillsModal onClose={() => setSkillsOpen(false)} />
      )}
    </div>
  );
}

// ─── Skills viewer modal ─────────────────────────────────────────────

type SkillSummary = { name: string; title: string; bytes: number; path: string };
type SkillContent = { name: string; title: string; path: string; content: string };

function SkillsModal({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<SkillSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<SkillContent | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/hermes/skills`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: SkillSummary[]) => {
        setList(data);
        if (data[0]) setSelected(data[0].name);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setContent(null);
    fetch(`${API_URL}/api/v1/hermes/skills/${selected}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: SkillContent) => setContent(data))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, [selected]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex h-[85vh] max-h-[800px] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Hermes skills</h2>
            <p className="text-xs text-zinc-500">
              Installed skills available to the autoresearch ratchet + chat agent.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-zinc-800 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-900"
          >
            Close ✕
          </button>
        </div>

        {err && (
          <div className="border-b border-rose-900/50 bg-rose-950/30 px-4 py-2 font-mono text-xs text-rose-300">
            {err}
          </div>
        )}

        <div className="grid flex-1 grid-cols-[16rem_1fr] overflow-hidden">
          {/* Left: skill list */}
          <aside className="overflow-y-auto border-r border-zinc-800 bg-zinc-900/40">
            {list === null && !err ? (
              <div className="p-4 text-sm text-zinc-500">Loading…</div>
            ) : list && list.length === 0 ? (
              <div className="p-4 text-sm text-zinc-500">No skills installed.</div>
            ) : (
              <ul>
                {(list ?? []).map((s) => (
                  <li key={s.name}>
                    <button
                      onClick={() => setSelected(s.name)}
                      className={`block w-full border-b border-zinc-800/60 px-3 py-2 text-left text-sm transition-colors ${
                        selected === s.name
                          ? 'bg-emerald-950/30 text-emerald-200'
                          : 'text-zinc-300 hover:bg-zinc-800/50'
                      }`}
                    >
                      <div className="font-medium">{s.title}</div>
                      <div className="font-mono text-[10px] text-zinc-500">
                        {s.name} · {Math.round(s.bytes / 1024)} KB
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>

          {/* Right: markdown viewer */}
          <main className="overflow-y-auto p-4">
            {content === null ? (
              <div className="text-sm text-zinc-500">Pick a skill on the left.</div>
            ) : (
              <>
                <div className="mb-2 font-mono text-[10px] text-zinc-500">
                  {content.path}
                </div>
                <pre className="whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-zinc-200">
                  {content.content}
                </pre>
              </>
            )}
          </main>
        </div>
      </div>
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
