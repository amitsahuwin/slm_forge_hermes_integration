import { useState } from 'react';
import { API_URL } from '../lib/api';

export type SkillResponse = {
  skill: string;
  model: string;
  parsed: Record<string, unknown> | null;
  raw: string;
  elapsed_ms: number;
};

type Props = {
  /** Endpoint path, joined onto API_URL. Must start with "/". */
  path: string;
  /** Optional request body. If omitted, no body is sent. */
  body?: unknown;
  /** Button label when idle. */
  label: string;
  /** Optional emoji shown to the left of the label. */
  emoji?: string;
  /** Tailwind tone — emerald (default), amber, rose, zinc. */
  tone?: 'emerald' | 'amber' | 'rose' | 'zinc';
  /** Called when the skill returns; receives the parsed response. */
  onResult: (resp: SkillResponse) => void;
  /** Optional: called when the user clears the result (parent should hide cards). */
  onClear?: () => void;
  /** Disable from the outside (e.g. while a sibling action is running). */
  disabled?: boolean;
  /** Compact = small chip. Otherwise normal button size. */
  size?: 'sm' | 'md';
};

const TONE: Record<NonNullable<Props['tone']>, string> = {
  emerald: 'border-hcl-teal/30 bg-hcl-teal/10 text-hcl-dark-teal hover:bg-hcl-dark-teal/30',
  amber: 'border-hcl-warning/50 bg-hcl-warning/10 text-hcl-warning hover:bg-hcl-warning/10',
  rose: 'border-hcl-error/50 bg-red-50 text-red-600 hover:bg-hcl-error/10',
  zinc: 'border-hcl-teal/30 text-hcl-dark hover:bg-hcl-tech-grey',
};

/**
 * Generic "call one Hermes skill endpoint and bubble the result up" button.
 * Keeps the eight N.4 touchpoints DRY — each page only needs to render this
 * + decide how to display the result.
 */
export default function HermesSkillButton({
  path,
  body,
  label,
  emoji,
  tone = 'zinc',
  onResult,
  onClear,
  disabled,
  size = 'md',
}: Props) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    onClear?.();
    try {
      const init: RequestInit = { method: 'POST' };
      if (body !== undefined) {
        init.headers = { 'Content-Type': 'application/json' };
        init.body = JSON.stringify(body);
      }
      const r = await fetch(`${API_URL}${path}`, init);
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      onResult((await r.json()) as SkillResponse);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const sizeCls = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1.5 text-xs';

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={run}
        disabled={running || disabled}
        className={`rounded border ${sizeCls} ${TONE[tone]} disabled:cursor-not-allowed disabled:opacity-50`}
      >
        {running ? 'Asking Hermes…' : `${emoji ? emoji + ' ' : ''}${label}`}
      </button>
      {error && <span className="text-[11px] text-red-600">{error}</span>}
    </span>
  );
}
