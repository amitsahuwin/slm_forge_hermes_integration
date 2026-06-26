import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import HermesSkillButton, {
  type SkillResponse,
} from '../components/HermesSkillButton';
import SynthesizeButton from '../components/SynthesizeButton';
import { API_URL } from '../lib/api';
import {
  type DatasetDetail as DDetail,
  type RowsResponse,
  type SplitName,
  datasetsApi,
} from '../lib/datasets-api';

type QualityIssue = {
  severity: 'high' | 'medium' | 'low';
  kind: string;
  description: string;
  affected_count?: number;
  fix?: string;
};

type QualityReport = {
  overall_health?: 'good' | 'fair' | 'poor';
  summary?: string;
  issues?: QualityIssue[];
  ready_to_train?: boolean;
};

type CanaryProposal = {
  canary?: Record<string, unknown>[];
  rationale?: string[];
};

const PAGE_SIZE = 20;

export default function DatasetDetail() {
  const { name } = useParams<{ name: string }>();
  const [detail, setDetail] = useState<DDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [split, setSplit] = useState<SplitName>('train');
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<RowsResponse | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);

  // Phase N.4 — Hermes panels
  const [quality, setQuality] = useState<QualityReport | null>(null);
  const [qualityRaw, setQualityRaw] = useState<string | null>(null);
  const [canary, setCanary] = useState<CanaryProposal | null>(null);
  const [canaryRaw, setCanaryRaw] = useState<string | null>(null);
  const [savingCanary, setSavingCanary] = useState(false);
  const [canarySaveResult, setCanarySaveResult] = useState<string | null>(null);

  async function saveCanary() {
    if (!name || !canary?.canary?.length) return;
    setSavingCanary(true);
    setCanarySaveResult(null);
    try {
      const r = await fetch(
        `${API_URL}/api/v1/hermes/propose-canary/${encodeURIComponent(name)}/save`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ canary: canary.canary }),
        },
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const j = (await r.json()) as { saved: string; count: number };
      setCanarySaveResult(
        `Saved ${j.count} canary records to ${j.saved.split('/').pop()}`,
      );
    } catch (e: unknown) {
      setCanarySaveResult(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSavingCanary(false);
    }
  }

  // Fetch detail once per name.
  useEffect(() => {
    if (!name) return;
    let alive = true;
    setDetail(null);
    setError(null);
    datasetsApi
      .getDetail(name)
      .then((d) => alive && setDetail(d))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [name]);

  // Fetch paginated rows whenever split/offset changes.
  useEffect(() => {
    if (!name) return;
    let alive = true;
    setRowsError(null);
    datasetsApi
      .getRows(name, split, offset, PAGE_SIZE)
      .then((r) => alive && setRows(r))
      .catch((e: unknown) => alive && setRowsError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [name, split, offset]);

  // Reset offset when switching split.
  function switchSplit(s: SplitName) {
    setSplit(s);
    setOffset(0);
    setRows(null);
  }

  if (error)
    return <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>;
  if (!detail) return <div className="text-sm text-hcl-dark/50">Loading dataset {name}…</div>;

  const showFrom = rows ? (rows.total === 0 ? 0 : rows.offset + 1) : 0;
  const showTo = rows ? Math.min(rows.offset + rows.rows.length, rows.total) : 0;
  const total = rows?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = rows ? offset + PAGE_SIZE < rows.total : false;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to="/datasets" className="text-xs text-hcl-dark/50 hover:text-hcl-teal">
            ← all datasets
          </Link>
          <h1 className="mt-2 font-mono text-2xl font-semibold tracking-tight">
            {detail.name}
          </h1>
          {detail.description && (
            <p className="mt-1 text-sm text-hcl-dark/60">{detail.description}</p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <HermesSkillButton
              path={`/api/v1/hermes/data-quality/${encodeURIComponent(detail.name)}`}
              label="Review quality"
              emoji="🔬"
              tone="zinc"
              onResult={(r: SkillResponse) => {
                setQuality((r.parsed as QualityReport) ?? null);
                setQualityRaw(r.parsed ? null : r.raw);
              }}
              onClear={() => {
                setQuality(null);
                setQualityRaw(null);
              }}
            />
            {!detail.has_canary && (
              <HermesSkillButton
                path={`/api/v1/hermes/propose-canary/${encodeURIComponent(detail.name)}`}
                label="Propose canary set"
                emoji="🎯"
                tone="zinc"
                onResult={(r: SkillResponse) => {
                  setCanary((r.parsed as CanaryProposal) ?? null);
                  setCanaryRaw(r.parsed ? null : r.raw);
                  setCanarySaveResult(null);
                }}
                onClear={() => {
                  setCanary(null);
                  setCanaryRaw(null);
                  setCanarySaveResult(null);
                }}
              />
            )}
          </div>
        </div>
        <SynthesizeButton
          dataset={detail.name}
          count={detail.train_count + detail.valid_count}
          variant="header"
        />
      </div>

      {/* Phase N.4 — quality report */}
      {(quality || qualityRaw) && (
        <section className="rounded-lg border border-hcl-warning/40 bg-hcl-warning/10 p-4 space-y-3 text-xs">
          <div className="flex items-baseline justify-between">
            <h3 className="font-medium uppercase tracking-wider text-hcl-warning">
              🔬 Data quality review
            </h3>
            {quality?.overall_health && (
              <span
                className={`rounded-full px-2 py-0.5 font-mono ${
                  quality.overall_health === 'good'
                    ? 'bg-hcl-teal/10 text-hcl-teal'
                    : quality.overall_health === 'fair'
                    ? 'bg-hcl-warning/10 text-hcl-warning'
                    : 'bg-red-50 text-red-600'
                }`}
              >
                {quality.overall_health}
              </span>
            )}
          </div>
          {quality?.summary && (
            <p className="italic text-hcl-dark/80">{quality.summary}</p>
          )}
          {quality?.issues?.length ? (
            <ul className="space-y-2">
              {quality.issues.map((it, i) => (
                <li
                  key={i}
                  className="rounded-md bg-hcl-bg px-3 py-2"
                >
                  <div className="flex items-baseline gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                        it.severity === 'high'
                          ? 'bg-red-50 text-red-600'
                          : it.severity === 'medium'
                          ? 'bg-hcl-warning/10 text-hcl-warning'
                          : 'bg-hcl-tech-grey text-hcl-dark/60'
                      }`}
                    >
                      {it.severity}
                    </span>
                    <span className="font-mono text-hcl-dark/60">{it.kind}</span>
                    {it.affected_count != null && (
                      <span className="text-hcl-dark/50">· {it.affected_count} affected</span>
                    )}
                  </div>
                  <div className="mt-1 text-hcl-dark/80">{it.description}</div>
                  {it.fix && (
                    <div className="mt-1 text-hcl-teal">→ {it.fix}</div>
                  )}
                </li>
              ))}
            </ul>
          ) : qualityRaw ? (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
              {qualityRaw}
            </pre>
          ) : (
            <p className="text-hcl-dark/50">No issues reported.</p>
          )}
        </section>
      )}

      {/* Phase N.4 — proposed canary set */}
      {(canary || canaryRaw) && (
        <section className="rounded-lg border border-hcl-teal/30 bg-hcl-teal/5 p-4 space-y-3 text-xs">
          <div className="flex items-baseline justify-between">
            <h3 className="font-medium uppercase tracking-wider text-hcl-teal">
              🎯 Proposed canary set
            </h3>
            {canary?.canary?.length ? (
              <button
                type="button"
                onClick={saveCanary}
                disabled={savingCanary}
                className="rounded border border-hcl-teal/30 bg-hcl-teal/10 px-2 py-1 text-hcl-dark-teal hover:bg-hcl-dark-teal/30 disabled:opacity-50"
              >
                {savingCanary
                  ? 'Saving…'
                  : `Save ${canary.canary.length} → canary.jsonl`}
              </button>
            ) : null}
          </div>
          {canarySaveResult && (
            <div
              className={`rounded px-3 py-2 ${
                canarySaveResult.startsWith('Save failed')
                  ? 'bg-red-50 text-red-600'
                  : 'bg-hcl-teal/10 text-hcl-dark-teal'
              }`}
            >
              {canarySaveResult}
            </div>
          )}
          {canary?.canary?.length ? (
            <ol className="space-y-2">
              {canary.canary.map((r, i) => (
                <li key={i} className="rounded-md bg-hcl-bg px-3 py-2">
                  <pre className="max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
                    {JSON.stringify(r, null, 2)}
                  </pre>
                  {canary.rationale?.[i] && (
                    <p className="mt-1 italic text-hcl-dark/60">{canary.rationale[i]}</p>
                  )}
                </li>
              ))}
            </ol>
          ) : canaryRaw ? (
            <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
              {canaryRaw}
            </pre>
          ) : null}
        </section>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="train rows" value={detail.train_count.toLocaleString()} />
        <Stat label="valid rows" value={detail.valid_count.toLocaleString()} />
        <Stat
          label="canary rows"
          value={detail.has_canary ? detail.canary_count.toLocaleString() : '—'}
        />
        <Stat
          label="median chars"
          value={detail.length_stats.p50.toLocaleString()}
          hint={`p90 ${detail.length_stats.p90.toLocaleString()} · max ${detail.length_stats.max.toLocaleString()}`}
        />
      </section>

      {detail.readme_markdown && (
        <section className="rounded-lg border border-hcl-light-blue bg-white p-4">
          <h2 className="mb-3 font-mono text-xs uppercase tracking-wider text-hcl-dark/50">
            README
          </h2>
          <div className="prose prose-invert prose-sm max-w-none">
            <MiniMarkdown source={detail.readme_markdown} />
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center gap-6 border-b border-hcl-light-blue">
          <TabButton active={split === 'train'} onClick={() => switchSplit('train')}>
            Train ({detail.train_count})
          </TabButton>
          <TabButton active={split === 'valid'} onClick={() => switchSplit('valid')}>
            Valid ({detail.valid_count})
          </TabButton>
          {detail.has_canary && (
            <TabButton active={split === 'canary'} onClick={() => switchSplit('canary')}>
              Canary ({detail.canary_count})
            </TabButton>
          )}
        </div>

        <div className="mt-4 space-y-3">
          {rowsError && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
              {rowsError}
            </div>
          )}
          {rows === null && !rowsError && (
            <div className="text-sm text-hcl-dark/50">Loading rows…</div>
          )}
          {rows && rows.rows.length === 0 && (
            <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-10 text-center text-sm text-hcl-dark/50">
              No rows in this split.
            </div>
          )}
          {rows &&
            rows.rows.map((row, idx) => (
              <RowCard key={offset + idx} row={row} index={offset + idx} />
            ))}
        </div>

        {rows && rows.total > 0 && (
          <div className="mt-5 flex items-center justify-between">
            <div className="font-mono text-xs text-hcl-dark/50">
              Showing {showFrom.toLocaleString()}–{showTo.toLocaleString()} of{' '}
              {total.toLocaleString()}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!canPrev}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-md border border-hcl-light-blue bg-white px-3 py-1.5 text-xs text-hcl-dark/80 hover:bg-hcl-tech-grey disabled:cursor-not-allowed disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                disabled={!canNext}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-md border border-hcl-light-blue bg-white px-3 py-1.5 text-xs text-hcl-dark/80 hover:bg-hcl-tech-grey disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

// ─── helpers ────────────────────────────────────────────────────────

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-hcl-light-blue bg-white px-3 py-2.5">
      <div className="font-mono text-xs text-hcl-dark/50">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-hcl-dark">{value}</div>
      {hint && <div className="mt-0.5 font-mono text-[10px] text-hcl-dark/40">{hint}</div>}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px border-b-2 px-1 pb-2.5 font-mono text-sm transition-colors ${
        active
          ? 'border-hcl-teal text-hcl-teal'
          : 'border-transparent text-hcl-dark/50 hover:text-hcl-dark/80'
      }`}
    >
      {children}
    </button>
  );
}

type ChatMessage = { role: string; content: string };

function isChatRow(row: Record<string, unknown>): row is { messages: ChatMessage[] } {
  const m = row.messages;
  if (!Array.isArray(m)) return false;
  return m.every(
    (x) =>
      x !== null &&
      typeof x === 'object' &&
      typeof (x as { role?: unknown }).role === 'string' &&
      typeof (x as { content?: unknown }).content === 'string',
  );
}

function roleColor(role: string): string {
  if (role === 'user') return 'text-hcl-teal';
  if (role === 'assistant') return 'text-hcl-info';
  if (role === 'system') return 'text-hcl-dark/60';
  return 'text-hcl-dark/50';
}

function RowCard({ row, index }: { row: Record<string, unknown>; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const chat = isChatRow(row) ? row.messages : null;

  return (
    <div className="rounded-lg border border-hcl-light-blue bg-white p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-wider text-hcl-dark/40">
          #{index}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="font-mono text-[10px] text-hcl-dark/50 hover:text-hcl-teal"
        >
          {expanded ? 'show less' : 'show more'}
        </button>
      </div>
      {chat ? (
        <div className={`space-y-2 ${expanded ? '' : 'max-h-32 overflow-hidden'}`}>
          {chat.map((m, i) => (
            <div key={i} className="font-mono text-xs">
              <span className={`font-semibold ${roleColor(m.role)}`}>{m.role}: </span>
              <span className="whitespace-pre-wrap text-hcl-dark/80">{m.content}</span>
            </div>
          ))}
        </div>
      ) : (
        <pre
          className={`whitespace-pre-wrap font-mono text-xs text-hcl-dark/80 ${
            expanded ? '' : 'max-h-32 overflow-hidden'
          }`}
        >
          {JSON.stringify(row, null, 2)}
        </pre>
      )}
    </div>
  );
}

/**
 * Minimal inline markdown renderer (no deps).
 * Handles: headings (# .. ######), **bold**, *italic*, `code`,
 * fenced ```code blocks```, blank-line paragraph breaks, line breaks.
 */
function MiniMarkdown({ source }: { source: string }) {
  const blocks = useMemo(() => parseMarkdown(source), [source]);
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === 'code') {
          return (
            <pre
              key={i}
              className="overflow-x-auto rounded-md border border-hcl-light-blue bg-hcl-bg/60 p-3 font-mono text-xs text-hcl-dark/80"
            >
              {b.text}
            </pre>
          );
        }
        if (b.type === 'heading') {
          const sizes = ['text-xl', 'text-lg', 'text-base', 'text-sm', 'text-sm', 'text-sm'];
          const cls = `mt-3 mb-2 font-semibold text-hcl-dark ${sizes[b.level - 1] ?? 'text-sm'}`;
          if (b.level === 1) return <h1 key={i} className={cls}>{renderInline(b.text)}</h1>;
          if (b.level === 2) return <h2 key={i} className={cls}>{renderInline(b.text)}</h2>;
          if (b.level === 3) return <h3 key={i} className={cls}>{renderInline(b.text)}</h3>;
          if (b.level === 4) return <h4 key={i} className={cls}>{renderInline(b.text)}</h4>;
          if (b.level === 5) return <h5 key={i} className={cls}>{renderInline(b.text)}</h5>;
          return <h6 key={i} className={cls}>{renderInline(b.text)}</h6>;
        }
        // paragraph
        return (
          <p key={i} className="my-2 text-sm leading-relaxed text-hcl-dark/80">
            {b.lines.map((ln, j) => (
              <span key={j}>
                {renderInline(ln)}
                {j < b.lines.length - 1 && <br />}
              </span>
            ))}
          </p>
        );
      })}
    </>
  );
}

type MdBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'code'; text: string }
  | { type: 'paragraph'; lines: string[] };

function parseMarkdown(src: string): MdBlock[] {
  const lines = src.split(/\r?\n/);
  const out: MdBlock[] = [];
  let i = 0;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push({ type: 'paragraph', lines: para });
      para = [];
    }
  };

  while (i < lines.length) {
    const ln = lines[i];
    if (ln.trim().startsWith('```')) {
      flushPara();
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing fence
      out.push({ type: 'code', text: buf.join('\n') });
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(ln);
    if (h) {
      flushPara();
      out.push({ type: 'heading', level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    if (ln.trim() === '') {
      flushPara();
      i++;
      continue;
    }
    para.push(ln);
    i++;
  }
  flushPara();
  return out;
}

/** Inline markdown: **bold**, *italic*, `code`. Returns ReactNode array. */
function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('**')) {
      nodes.push(
        <strong key={`k${key++}`} className="font-semibold text-hcl-dark">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else if (tok.startsWith('`')) {
      nodes.push(
        <code
          key={`k${key++}`}
          className="rounded bg-hcl-tech-grey px-1 py-0.5 font-mono text-xs text-hcl-teal"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else {
      nodes.push(
        <em key={`k${key++}`} className="italic">
          {tok.slice(1, -1)}
        </em>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
