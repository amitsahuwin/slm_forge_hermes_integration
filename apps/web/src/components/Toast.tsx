/**
 * Toast renderer. Mount a single <ToastContainer /> near the root.
 *
 * Subscribes to the singleton bus in `lib/toast.ts`. No external deps —
 * pure Tailwind animations.
 */
import { useEffect, useState } from 'react';
import { toast, type ToastMessage } from '../lib/toast';

function kindClasses(kind: ToastMessage['kind']): string {
  switch (kind) {
    case 'error':
      return 'border-red-800 bg-red-950/80 text-red-100';
    case 'success':
      return 'border-emerald-800 bg-emerald-950/80 text-emerald-100';
    default:
      return 'border-zinc-700 bg-zinc-900/90 text-zinc-100';
  }
}

export function ToastContainer() {
  const [msgs, setMsgs] = useState<ToastMessage[]>([]);

  useEffect(() => toast.subscribe(setMsgs), []);

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-80 flex-col gap-2">
      {msgs.map((m) => (
        <div
          key={m.id}
          className={`pointer-events-auto rounded-xl border px-4 py-3 text-sm shadow-lg backdrop-blur transition-all duration-300 animate-[toast-slide_240ms_ease-out] ${kindClasses(
            m.kind,
          )}`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1 whitespace-pre-wrap break-words">{m.text}</div>
            <button
              onClick={() => toast.dismiss(m.id)}
              className="-mr-1 -mt-1 rounded p-1 text-current opacity-60 hover:opacity-100"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        </div>
      ))}
      <style>{`
        @keyframes toast-slide {
          from { transform: translateX(110%); opacity: 0; }
          to   { transform: translateX(0);    opacity: 1; }
        }
      `}</style>
    </div>
  );
}
