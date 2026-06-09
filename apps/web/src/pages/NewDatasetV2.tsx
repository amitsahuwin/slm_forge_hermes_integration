import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_URL } from '../lib/api';

// ─── Types ─────────────────────────────────────────────────────────

type PreviewResp = {
  format: string;
  conversion: 'direct' | 'ollama';
  sample_records: Record<string, unknown>[];
  total_records: number;
  predicted_train: number;
  predicted_valid: number;
  predicted_canary: number;
  warnings: string[];
};

type FileResp = {
  name: string;
  train: number;
  valid: number;
  canary: number;
  format: string;
  conversion: 'direct' | 'ollama';
};

type SourceType = 'file' | 'url' | 'scrape' | 's3';

const SOURCE_META: Record<SourceType, { label: string; sub: string }> = {
  file: { label: 'File', sub: 'Upload from your computer' },
  url: { label: 'URL', sub: 'Fetch a remote JSONL / CSV / JSON' },
  scrape: { label: 'Web scrape', sub: 'Extract article text from any page' },
  s3: { label: 'S3', sub: 'Pull an object from an S3 bucket' },
};

// ─── Page ──────────────────────────────────────────────────────────

export default function NewDatasetV2() {
  const navigate = useNavigate();
  const [source, setSource] = useState<SourceType>('file');

  // Shared
  const [name, setName] = useState('');
  const [forceOllama, setForceOllama] = useState(false);
  const [preview, setPreview] = useState<PreviewResp | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // File-source state
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);

  // URL / scrape state (reused across both)
  const [url, setUrl] = useState('');

  // S3 state
  const [s3Path, setS3Path] = useState('');
  const [s3Key, setS3Key] = useState('');
  const [s3Secret, setS3Secret] = useState('');
  const [s3Region, setS3Region] = useState('us-east-1');

  // Switching tabs clears the preview so we don't show stale info.
  useEffect(() => {
    setPreview(null);
    setError(null);
    setStatus(null);
  }, [source]);

  // ─── Preview dispatch ──────────────────────────────────────

  const runPreview = useCallback(
    async (force: boolean) => {
      setPreviewing(true);
      setError(null);
      setStatus('Detecting format…');
      try {
        let r: Response;
        if (source === 'file') {
          if (!file) throw new Error('Select a file first');
          const fd = new FormData();
          fd.append('file', file);
          fd.append('force_ollama', String(force));
          r = await fetch(`${API_URL}/api/v1/ingest/preview`, {
            method: 'POST',
            body: fd,
          });
        } else if (source === 'url' || source === 'scrape') {
          if (!url) throw new Error('URL required');
          const endpoint =
            source === 'url' ? 'from-url' : 'from-scrape';
          r = await fetch(`${API_URL}/api/v1/ingest/${endpoint}/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, force_ollama: force }),
          });
        } else {
          if (!s3Path) throw new Error('S3 path required');
          r = await fetch(`${API_URL}/api/v1/ingest/from-s3/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              s3_path: s3Path,
              access_key: s3Key || undefined,
              secret_key: s3Secret || undefined,
              region: s3Region || undefined,
              force_ollama: force,
            }),
          });
        }
        if (!r.ok) {
          const detail = await safeError(r);
          throw new Error(`Preview failed: ${detail}`);
        }
        setPreview((await r.json()) as PreviewResp);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        setPreview(null);
      } finally {
        setPreviewing(false);
        setStatus(null);
      }
    },
    [source, file, url, s3Path, s3Key, s3Secret, s3Region],
  );

  // ─── File-tab helpers ──────────────────────────────────────

  function onFile(f: File | null) {
    setFile(f);
    setPreview(null);
    setError(null);
    if (f) {
      setForceOllama(false);
      void runPreview(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  }

  // ─── Create (finalize) ──────────────────────────────────────

  async function onCreate() {
    if (!name) return;
    if (!preview) {
      setError('Click Preview first to validate the source.');
      return;
    }
    setSubmitting(true);
    setError(null);
    setStatus(
      preview.conversion === 'ollama'
        ? 'Converting via Ollama (this can take a minute)…'
        : 'Writing dataset…',
    );
    try {
      let r: Response;
      if (source === 'file') {
        if (!file) throw new Error('Select a file first');
        const fd = new FormData();
        fd.append('name', name);
        fd.append('file', file);
        fd.append('force_ollama', String(forceOllama));
        r = await fetch(`${API_URL}/api/v1/ingest/file`, {
          method: 'POST',
          body: fd,
        });
      } else if (source === 'url' || source === 'scrape') {
        const endpoint = source === 'url' ? 'from-url' : 'from-scrape';
        r = await fetch(`${API_URL}/api/v1/ingest/${endpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, url, force_ollama: forceOllama }),
        });
      } else {
        r = await fetch(`${API_URL}/api/v1/ingest/from-s3`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            s3_path: s3Path,
            access_key: s3Key || undefined,
            secret_key: s3Secret || undefined,
            region: s3Region || undefined,
            force_ollama: forceOllama,
          }),
        });
      }
      if (!r.ok) throw new Error(await safeError(r));
      const data = (await r.json()) as FileResp;
      setStatus(
        `Created '${data.name}' — ${data.train} train / ${data.valid} valid / ${data.canary} canary.`,
      );
      navigate(`/datasets/${data.name}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(null);
    } finally {
      setSubmitting(false);
    }
  }

  const nameValid = /^[a-z0-9][a-z0-9-_]*$/.test(name);
  const sourceReady =
    source === 'file'
      ? !!file
      : source === 'url' || source === 'scrape'
      ? !!url
      : !!s3Path;
  const canPreview = sourceReady && !previewing && !submitting;
  const canSubmit =
    !!name && nameValid && !!preview && !submitting && !previewing;

  // ─── Render ────────────────────────────────────────────────

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Dataset</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Pull data from any source. SLM-Forge will detect the format and, if
          needed, auto-convert it via Ollama into chat-style training records.
        </p>
      </div>

      {/* Source tabs */}
      <div role="tablist" className="flex gap-1 rounded-xl border border-zinc-800 bg-zinc-900/40 p-1">
        {(Object.keys(SOURCE_META) as SourceType[]).map((s) => {
          const active = s === source;
          return (
            <button
              key={s}
              role="tab"
              aria-selected={active}
              onClick={() => setSource(s)}
              className={`flex-1 rounded-lg px-3 py-2 text-sm transition-colors ${
                active
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
              }`}
            >
              <div className="font-medium">{SOURCE_META[s].label}</div>
              <div className="text-[10px] text-zinc-500">{SOURCE_META[s].sub}</div>
            </button>
          );
        })}
      </div>

      {error && (
        <div className="rounded-xl bg-rose-950/50 px-3 py-2 font-mono text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* Source-specific input(s) */}
      {source === 'file' && (
        <Field label="File (jsonl / json / csv / txt / md / anything)">
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-sm transition-colors ${
              dragOver
                ? 'border-emerald-500 bg-emerald-950/30 text-emerald-200'
                : 'border-zinc-800 bg-zinc-900/40 text-zinc-400 hover:border-zinc-700'
            }`}
          >
            <input
              type="file"
              className="hidden"
              onChange={(e) => onFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <>
                <span className="font-mono text-zinc-300">{file.name}</span>
                <span className="mt-1 text-xs text-zinc-500">
                  {(file.size / 1024).toFixed(1)} KB · click or drop to replace
                </span>
              </>
            ) : (
              <>
                <span>Drag a file here, or click to choose</span>
                <span className="mt-1 text-xs text-zinc-500">10 MB max</span>
              </>
            )}
          </label>
        </Field>
      )}

      {source === 'url' && (
        <Field label="URL (downloads the file as-is — jsonl, csv, json, txt)">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/data.jsonl"
            className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm focus:border-emerald-600 focus:outline-none"
          />
          <p className="mt-1 text-xs text-zinc-500">
            10 MB max. Follows redirects. Use scrape mode for HTML pages.
          </p>
        </Field>
      )}

      {source === 'scrape' && (
        <Field label="URL (extracts main article text via trafilatura)">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://blog.example.com/post/title"
            className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm focus:border-emerald-600 focus:outline-none"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Static HTML only. JS-rendered SPAs won't work — save the rendered
            page locally and use File upload instead.
          </p>
        </Field>
      )}

      {source === 's3' && (
        <div className="space-y-3">
          <Field label="S3 path">
            <input
              type="text"
              value={s3Path}
              onChange={(e) => setS3Path(e.target.value)}
              placeholder="s3://my-bucket/path/to/data.jsonl"
              className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm focus:border-emerald-600 focus:outline-none"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="AWS access key (optional — uses instance role if blank)">
              <input
                type="text"
                value={s3Key}
                onChange={(e) => setS3Key(e.target.value)}
                placeholder="AKIA…"
                className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-xs focus:border-emerald-600 focus:outline-none"
              />
            </Field>
            <Field label="AWS secret key">
              <input
                type="password"
                value={s3Secret}
                onChange={(e) => setS3Secret(e.target.value)}
                placeholder="••••••"
                className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-xs focus:border-emerald-600 focus:outline-none"
              />
            </Field>
          </div>
          <Field label="Region">
            <input
              type="text"
              value={s3Region}
              onChange={(e) => setS3Region(e.target.value)}
              placeholder="us-east-1"
              className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-xs focus:border-emerald-600 focus:outline-none"
            />
          </Field>
        </div>
      )}

      <Field label="Dataset name (lowercase, hyphens/underscores; folder name)">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-domain-qa"
          className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm focus:border-emerald-600 focus:outline-none"
        />
        {name && !nameValid && (
          <p className="mt-1 text-xs text-rose-400">
            Must start alphanumeric and contain only [a-z0-9-_].
          </p>
        )}
      </Field>

      {/* Preview trigger when not auto-previewed (URL / scrape / S3 don't auto-fire) */}
      {source !== 'file' && (
        <div>
          <button
            onClick={() => void runPreview(forceOllama)}
            disabled={!canPreview}
            className="rounded-xl border border-zinc-700 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {previewing ? 'Previewing…' : 'Preview'}
          </button>
        </div>
      )}

      {sourceReady && (
        <Field label="Conversion">
          <label className="flex items-center gap-2 text-sm text-zinc-300">
            <input
              type="checkbox"
              checked={forceOllama}
              onChange={(e) => {
                setForceOllama(e.target.checked);
                if (preview) void runPreview(e.target.checked);
              }}
              className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 accent-emerald-500"
            />
            <span>Force auto-convert via Ollama</span>
          </label>
          <p className="mt-1 text-xs text-zinc-500">
            On for messy text. Off when the source is already a known chat /
            prompt-completion format.
          </p>
        </Field>
      )}

      {status && (
        <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2 text-sm text-zinc-300">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
          {status}
        </div>
      )}

      {preview && (
        <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <div className="flex items-baseline justify-between">
            <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
              Preview
            </h3>
            <div className="font-mono text-xs text-zinc-500">
              {preview.total_records} record
              {preview.total_records === 1 ? '' : 's'} · {preview.format} ·{' '}
              {preview.conversion}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2 text-center text-xs">
            <Stat label="train" value={preview.predicted_train} />
            <Stat label="valid" value={preview.predicted_valid} />
            <Stat label="canary" value={preview.predicted_canary} />
          </div>

          {preview.warnings.length > 0 && (
            <ul className="space-y-1 rounded-md bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
              {preview.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}

          {preview.sample_records.length > 0 && (
            <div>
              <p className="mb-1 text-xs uppercase tracking-wider text-zinc-500">
                First {preview.sample_records.length} record
                {preview.sample_records.length === 1 ? '' : 's'}
              </p>
              <pre className="max-h-64 overflow-auto rounded-md bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
                {preview.sample_records
                  .map((r) => JSON.stringify(r, null, 2))
                  .join('\n\n')}
              </pre>
            </div>
          )}
        </section>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/datasets')}
          className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800"
        >
          ← Cancel
        </button>
        <button
          onClick={onCreate}
          disabled={!canSubmit}
          className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Creating…' : 'Create dataset'}
        </button>
      </div>
    </div>
  );
}

// ─── Subviews ──────────────────────────────────────────────────────

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2">
      <div className="font-mono text-lg text-zinc-100">{value}</div>
      <div className="text-xs uppercase tracking-wider text-zinc-500">{label}</div>
    </div>
  );
}

async function safeError(r: Response): Promise<string> {
  try {
    const j = (await r.json()) as { detail?: string };
    if (j.detail) return j.detail;
  } catch {
    /* ignore */
  }
  return `HTTP ${r.status}`;
}
