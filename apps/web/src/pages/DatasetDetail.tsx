import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  type DatasetDetail as DDetail,
  type RowsResponse,
  type SplitName,
  datasetsApi,
} from '../lib/datasets-api';

const PAGE_SIZE = 20;

export default function DatasetDetail() {
  const { name } = useParams<{ name: string }>();
  const [detail, setDetail] = useState<DDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [split, setSplit] = useState<SplitName>('train');
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<RowsResponse | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);

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
    return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{error}</div>;
  if (!detail) return <div className="text-sm text-zinc-500">Loading dataset {name}…</div>;

  const showFrom = rows ? (rows.total === 0 ? 0 : rows.offset + 1) : 0;
  const showTo = rows ? Math.min(rows.offset + rows.rows.length, rows.total) : 0;
  const total = rows?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = rows ? offset + PAGE_SIZE < rows.total : false;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/datasets" className="text-xs text-zinc-500 hover:text-emerald-400">
          ← all datasets
        </Link>
        <h1 className="mt-2 font-mono text-2xl font-semibold tracking-tight">{detail.name}</h1>
        {detail.description && (
          <p className="mt-1 text-sm text-zinc-400">{detail.description}</p>
        )}
      </div>

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
        <section className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="mb-3 font-mono text-xs uppercase tracking-wider text-zinc-500">
            README
          </h2>
          <div className="prose prose-invert prose-sm max-w-none">
            <MiniMarkdown source={detail.readme_markdown} />
          </div>
        </section>
      )}

      <section>
        <div className="flex items-center gap-6 border-b border-zinc-800">
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
            <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">
              {rowsError}
            </div>
          )}
          {rows === null && !rowsError && (
            <div className="text-sm text-zinc-500">Loading rows…</div>
          )}
          {rows && rows.rows.length === 0 && (
            <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-10 text-center text-sm text-zinc-500">
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
            <div className="font-mono text-xs text-zinc-500">
              Showing {showFrom.toLocaleString()}–{showTo.toLocaleString()} of{' '}
              {total.toLocaleString()}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!canPrev}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                disabled={!canNext}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-md border border-zinc-800 bg-zinc-900/40 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
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
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
      <div className="font-mono text-xs text-zinc-500">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-zinc-100">{value}</div>
      {hint && <div className="mt-0.5 font-mono text-[10px] text-zinc-600">{hint}</div>}
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
          ? 'border-emerald-500 text-emerald-300'
          : 'border-transparent text-zinc-500 hover:text-zinc-300'
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
  if (role === 'user') return 'text-emerald-400';
  if (role === 'assistant') return 'text-sky-400';
  if (role === 'system') return 'text-zinc-400';
  return 'text-zinc-500';
}

function RowCard({ row, index }: { row: Record<string, unknown>; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const chat = isChatRow(row) ? row.messages : null;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-wider text-zinc-600">
          #{index}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="font-mono text-[10px] text-zinc-500 hover:text-emerald-400"
        >
          {expanded ? 'show less' : 'show more'}
        </button>
      </div>
      {chat ? (
        <div className={`space-y-2 ${expanded ? '' : 'max-h-32 overflow-hidden'}`}>
          {chat.map((m, i) => (
            <div key={i} className="font-mono text-xs">
              <span className={`font-semibold ${roleColor(m.role)}`}>{m.role}: </span>
              <span className="whitespace-pre-wrap text-zinc-300">{m.content}</span>
            </div>
          ))}
        </div>
      ) : (
        <pre
          className={`whitespace-pre-wrap font-mono text-xs text-zinc-300 ${
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
              className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-950/60 p-3 font-mono text-xs text-zinc-300"
            >
              {b.text}
            </pre>
          );
        }
        if (b.type === 'heading') {
          const sizes = ['text-xl', 'text-lg', 'text-base', 'text-sm', 'text-sm', 'text-sm'];
          const cls = `mt-3 mb-2 font-semibold text-zinc-100 ${sizes[b.level - 1] ?? 'text-sm'}`;
          if (b.level === 1) return <h1 key={i} className={cls}>{renderInline(b.text)}</h1>;
          if (b.level === 2) return <h2 key={i} className={cls}>{renderInline(b.text)}</h2>;
          if (b.level === 3) return <h3 key={i} className={cls}>{renderInline(b.text)}</h3>;
          if (b.level === 4) return <h4 key={i} className={cls}>{renderInline(b.text)}</h4>;
          if (b.level === 5) return <h5 key={i} className={cls}>{renderInline(b.text)}</h5>;
          return <h6 key={i} className={cls}>{renderInline(b.text)}</h6>;
        }
        // paragraph
        return (
          <p key={i} className="my-2 text-sm leading-relaxed text-zinc-300">
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
        <strong key={`k${key++}`} className="font-semibold text-zinc-100">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else if (tok.startsWith('`')) {
      nodes.push(
        <code
          key={`k${key++}`}
          className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-xs text-emerald-300"
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
