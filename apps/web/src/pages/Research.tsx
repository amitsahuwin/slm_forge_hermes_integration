import { useCallback, useEffect, useMemo, useState } from 'react';
import ResearchModal from '../components/ResearchModal';
import { API_URL } from '../lib/api';

type ReportRow = {
  filename: string;
  title: string;
  topic: string;
  depth: string;
  generated_at: string;
  tags: string[];
  bytes: number;
};

type ReportContent = {
  filename: string;
  markdown: string;
};

const POLL_MS = 5000;

export default function Research() {
  const [reports, setReports] = useState<ReportRow[] | null>(null);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<ReportContent | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // ─── Reports list ──────────────────────────────────────────────────
  const loadReports = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/research/reports`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as ReportRow[];
      setReports(data);
      setReportsError(null);
      // Auto-select newest if nothing chosen.
      setSelected((cur) => {
        if (cur && data.some((d) => d.filename === cur)) return cur;
        return data[0]?.filename ?? null;
      });
    } catch (e) {
      setReportsError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void loadReports();
    const id = window.setInterval(loadReports, POLL_MS);
    return () => window.clearInterval(id);
  }, [loadReports]);

  // ─── Selected report content ───────────────────────────────────────
  useEffect(() => {
    if (!selected) {
      setContent(null);
      setContentError(null);
      return;
    }
    let alive = true;
    setContent(null);
    setContentError(null);
    fetch(`${API_URL}/api/v1/research/reports/${encodeURIComponent(selected)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as ReportContent;
      })
      .then((c) => alive && setContent(c))
      .catch((e: unknown) => alive && setContentError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [selected]);

  async function handleDelete(filename: string) {
    if (!window.confirm(`Delete "${filename}"? This cannot be undone.`)) return;
    try {
      const r = await fetch(
        `${API_URL}/api/v1/research/reports/${encodeURIComponent(filename)}`,
        { method: 'DELETE' },
      );
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
      if (selected === filename) setSelected(null);
      await loadReports();
    } catch (e) {
      window.alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const isEmpty = reports !== null && reports.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">R&D</h1>
          <p className="mt-1 text-sm text-hcl-dark/60">
            Ollama-generated market research reports. New reports appear here automatically.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-hcl-dark-teal px-3 py-1.5 text-sm font-medium text-white hover:bg-hcl-teal"
        >
          + New report
        </button>
      </div>

      {reportsError && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
          {reportsError}
        </div>
      )}

      {isEmpty && <EmptyState onCta={() => setModalOpen(true)} />}

      {!isEmpty && (
        <div className="flex gap-6">
          {/* Left rail */}
          <aside className="w-72 shrink-0">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-hcl-dark/50">
              Reports {reports ? `(${reports.length})` : ''}
            </div>
            {reports === null && (
              <div className="text-sm text-hcl-dark/50">Loading…</div>
            )}
            {reports && (
              <ul className="space-y-1.5">
                {reports.map((r) => (
                  <li key={r.filename}>
                    <ReportRowCard
                      row={r}
                      active={selected === r.filename}
                      onSelect={() => setSelected(r.filename)}
                      onDelete={() => handleDelete(r.filename)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </aside>

          {/* Main pane */}
          <section className="min-w-0 flex-1">
            {!selected && (
              <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-12 text-center text-sm text-hcl-dark/50">
                Pick a report on the left.
              </div>
            )}
            {selected && contentError && (
              <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
                {contentError}
              </div>
            )}
            {selected && !content && !contentError && (
              <div className="text-sm text-hcl-dark/50">Loading report…</div>
            )}
            {content && <ReportView content={content} />}
          </section>
        </div>
      )}

      <ResearchModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onDone={async (res) => {
          await loadReports();
          setSelected(res.filename);
        }}
      />
    </div>
  );
}

// ─── Pieces ───────────────────────────────────────────────────────────

function EmptyState({ onCta }: { onCta: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-hcl-light-blue bg-hcl-tech-grey/30 px-6 py-14 text-center">
      <div className="mx-auto mb-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-hcl-teal/10 text-hcl-teal">
        <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path d="M9 2a1 1 0 011 1v6h6a1 1 0 110 2h-6v6a1 1 0 11-2 0v-6H2a1 1 0 110-2h6V3a1 1 0 011-1z" />
        </svg>
      </div>
      <h2 className="text-base font-semibold text-hcl-dark">No reports yet</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-hcl-dark/60">
        Trigger a market research run and the report will appear here. The local Ollama model
        builds an outline, drafts each section, and saves a markdown file you can browse.
      </p>
      <button
        type="button"
        onClick={onCta}
        className="mt-4 rounded-md bg-hcl-dark-teal px-4 py-2 text-sm font-medium text-white hover:bg-hcl-teal"
      >
        Generate your first report
      </button>
    </div>
  );
}

function ReportRowCard({
  row,
  active,
  onSelect,
  onDelete,
}: {
  row: ReportRow;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`group relative rounded-md border px-3 py-2 transition-colors ${
        active
          ? 'border-hcl-teal/30 bg-hcl-teal/10'
          : 'border-hcl-light-blue bg-white hover:border-hcl-teal/30'
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        className="block w-full text-left"
      >
        <div className="line-clamp-2 text-sm font-medium text-hcl-dark">{row.title}</div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {row.tags.slice(0, 3).map((t) => (
            <span
              key={t}
              className="rounded-sm bg-hcl-tech-grey/80 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-hcl-dark/60"
            >
              {t}
            </span>
          ))}
        </div>
        <div className="mt-1 font-mono text-[10px] text-hcl-dark/50">
          {formatRelative(row.generated_at)} · {fmtBytes(row.bytes)}
        </div>
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className="absolute right-1.5 top-1.5 rounded-sm p-1 text-hcl-dark/40 opacity-0 transition-opacity hover:bg-hcl-tech-grey hover:text-red-500 group-hover:opacity-100"
        aria-label={`Delete ${row.filename}`}
        title="Delete report"
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path
            fillRule="evenodd"
            d="M8 3a1 1 0 011-1h2a1 1 0 011 1v1h4a1 1 0 110 2h-1v10a2 2 0 01-2 2H6a2 2 0 01-2-2V6H3a1 1 0 010-2h4V3zm2 4a1 1 0 10-2 0v7a1 1 0 102 0V7zm4 0a1 1 0 10-2 0v7a1 1 0 102 0V7z"
            clipRule="evenodd"
          />
        </svg>
      </button>
    </div>
  );
}

function ReportView({ content }: { content: ReportContent }) {
  const { frontmatter, body } = useMemo(
    () => splitFrontmatter(content.markdown),
    [content.markdown],
  );

  return (
    <article className="space-y-4">
      <header className="rounded-lg border border-hcl-light-blue bg-white p-4">
        <div className="font-mono text-[10px] uppercase tracking-wider text-hcl-dark/50">
          {content.filename}
        </div>
        {frontmatter.topic && (
          <div className="mt-1 font-mono text-xs text-hcl-dark/60">
            <span className="text-hcl-dark/40">topic: </span>
            {frontmatter.topic}
          </div>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-3 font-mono text-[10px] text-hcl-dark/50">
          {frontmatter.depth && (
            <span>
              <span className="text-hcl-dark/40">depth </span>
              {frontmatter.depth}
            </span>
          )}
          {frontmatter.model && (
            <span>
              <span className="text-hcl-dark/40">model </span>
              {frontmatter.model}
            </span>
          )}
          {frontmatter.generated_at && (
            <span>
              <span className="text-hcl-dark/40">generated </span>
              {formatRelative(frontmatter.generated_at)}
            </span>
          )}
        </div>
      </header>
      <div className="prose prose-invert prose-sm max-w-none rounded-lg border border-hcl-light-blue bg-white px-5 py-4">
        <MiniMarkdown source={body} />
      </div>
    </article>
  );
}

// ─── Frontmatter split ────────────────────────────────────────────────

type FrontmatterFields = {
  title?: string;
  topic?: string;
  depth?: string;
  model?: string;
  generated_at?: string;
  tags?: string[];
};

function splitFrontmatter(src: string): { frontmatter: FrontmatterFields; body: string } {
  const m = /^---\s*\n([\s\S]*?)\n---\s*\n?/.exec(src);
  if (!m) return { frontmatter: {}, body: src };
  const block = m[1];
  const fm: FrontmatterFields = {};
  for (const raw of block.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf(':');
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const val = line.slice(idx + 1).trim();
    if (key === 'tags') {
      const inner = val.replace(/^\[|\]$/g, '');
      const items = inner
        .split(',')
        .map((s) => unquote(s.trim()))
        .filter(Boolean);
      fm.tags = items;
    } else {
      (fm as Record<string, string>)[key] = unquote(val);
    }
  }
  return { frontmatter: fm, body: src.slice(m[0].length) };
}

function unquote(s: string): string {
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    return s.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, '\\');
  }
  return s;
}

// ─── Markdown renderer (lifted from DatasetDetail.tsx with table support) ────

type MdBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'code'; text: string }
  | { type: 'paragraph'; lines: string[] }
  | { type: 'list'; items: string[] }
  | { type: 'table'; header: string[]; rows: string[][] };

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
          const cls = `mt-4 mb-2 font-semibold text-hcl-dark ${sizes[b.level - 1] ?? 'text-sm'}`;
          if (b.level === 1) return <h1 key={i} className={cls}>{renderInline(b.text)}</h1>;
          if (b.level === 2) return <h2 key={i} className={cls}>{renderInline(b.text)}</h2>;
          if (b.level === 3) return <h3 key={i} className={cls}>{renderInline(b.text)}</h3>;
          if (b.level === 4) return <h4 key={i} className={cls}>{renderInline(b.text)}</h4>;
          if (b.level === 5) return <h5 key={i} className={cls}>{renderInline(b.text)}</h5>;
          return <h6 key={i} className={cls}>{renderInline(b.text)}</h6>;
        }
        if (b.type === 'list') {
          return (
            <ul key={i} className="my-2 list-disc space-y-1 pl-5 text-sm text-hcl-dark/80">
              {b.items.map((it, j) => (
                <li key={j}>{renderInline(it)}</li>
              ))}
            </ul>
          );
        }
        if (b.type === 'table') {
          return (
            <div key={i} className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-hcl-light-blue">
                    {b.header.map((h, j) => (
                      <th
                        key={j}
                        className="bg-hcl-dark-blue px-2 py-1.5 font-mono text-[10px] uppercase tracking-wider text-white"
                      >
                        {renderInline(h)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {b.rows.map((row, j) => (
                    <tr key={j} className="border-b border-hcl-light-blue">
                      {row.map((cell, k) => (
                        <td key={k} className="px-2 py-1.5 align-top text-xs text-hcl-dark/80">
                          {renderInline(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
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
    // Fenced code.
    if (ln.trim().startsWith('```')) {
      flushPara();
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++;
      out.push({ type: 'code', text: buf.join('\n') });
      continue;
    }
    // Heading.
    const h = /^(#{1,6})\s+(.*)$/.exec(ln);
    if (h) {
      flushPara();
      out.push({ type: 'heading', level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    // Table: a header row of "| col | col |" followed by a separator "| --- | --- |".
    if (
      ln.trim().startsWith('|') &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])
    ) {
      flushPara();
      const header = splitTableRow(ln);
      i += 2; // skip header + separator
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i]));
        i++;
      }
      out.push({ type: 'table', header, rows });
      continue;
    }
    // Bullet list.
    if (/^\s*[-*]\s+/.test(ln)) {
      flushPara();
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      out.push({ type: 'list', items });
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

function splitTableRow(line: string): string[] {
  // Drop leading/trailing pipe and split on remaining pipes.
  const inner = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return inner.split('|').map((c) => c.trim());
}

function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
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
    } else if (tok.startsWith('[')) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (linkMatch) {
        nodes.push(
          <a
            key={`k${key++}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-hcl-teal underline hover:text-hcl-teal"
          >
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(tok);
      }
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

// ─── Misc ─────────────────────────────────────────────────────────────

function formatRelative(iso: string): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
