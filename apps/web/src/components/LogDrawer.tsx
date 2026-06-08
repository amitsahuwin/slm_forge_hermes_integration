import { useEffect } from 'react';
import LogPane, { type WorkerName } from './LogPane';

type LogDrawerProps = {
  worker: WorkerName | null;
  onClose: () => void;
};

const WORKER_TITLES: Record<WorkerName, string> = {
  api: 'API',
  trainer: 'Trainer',
  exporter: 'Exporter',
  ratchet: 'Ratchet',
};

const WORKER_SUBTITLES: Record<WorkerName, string> = {
  api: 'FastAPI control plane',
  trainer: 'MLX-LM LoRA worker',
  exporter: 'GGUF fuse + quantize worker',
  ratchet: 'Hermes autoresearch loop',
};

/**
 * Slide-in side drawer that streams the live log for a single worker.
 * State is owned by the parent (Dashboard); pass `worker={null}` to close.
 */
export default function LogDrawer({ worker, onClose }: LogDrawerProps) {
  // Esc-to-close. Bound once while the drawer is open.
  useEffect(() => {
    if (!worker) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [worker, onClose]);

  // Lock body scroll while open so the page behind doesn't drift.
  useEffect(() => {
    if (!worker) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [worker]);

  if (!worker) return null;

  return (
    <div
      className="fixed inset-0 z-50"
      role="dialog"
      aria-modal="true"
      aria-label={`${WORKER_TITLES[worker]} live log`}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-zinc-950/70 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />

      {/* Panel */}
      <div className="absolute inset-y-0 right-0 flex w-full max-w-[640px] flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
        <header className="flex items-center justify-between border-b border-zinc-800 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold tracking-tight text-zinc-100">
              {WORKER_TITLES[worker]}
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500">{WORKER_SUBTITLES[worker]}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close log drawer"
            className="rounded-lg border border-zinc-800 px-2.5 py-1 text-sm text-zinc-300 hover:border-zinc-700 hover:bg-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-600"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 overflow-hidden p-4">
          <LogPane worker={worker} height="calc(100vh - 6.5rem)" />
        </div>
      </div>
    </div>
  );
}
