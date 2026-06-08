import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { API_URL } from '../lib/api';

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

const JSONL_FORMATS = new Set(['jsonl_chat', 'jsonl_text', 'jsonl_pc']);

export default function NewDatasetV2() {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResp | null>(null);
  const [forceOllama, setForceOllama] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const runPreview = useCallback(
    async (f: File, force: boolean) => {
      setPreviewing(true);
      setError(null);
      setStatus('Detecting format…');
      try {
        const fd = new FormData();
        fd.append('file', f);
        fd.append('force_ollama', String(force));
        const r = await fetch(`${API_URL}/api/v1/ingest/preview`, {
          method: 'POST',
          body: fd,
        });
        if (!r.ok) {
          const detail = await safeError(r);
          throw new Error(`Preview failed: ${detail}`);
        }
        const data = (await r.json()) as PreviewResp;
        setPreview(data);
        // Default force_ollama ON for unknown / non-jsonl formats
        if (!JSONL_FORMATS.has(data.format) && data.conversion === 'direct') {
          // Keep user's manual choice; only auto-flip if they haven't touched it
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        setPreview(null);
      } finally {
        setPreviewing(false);
        setStatus(null);
      }
    },
    [],
  );

  function onFile(f: File | null) {
    setFile(f);
    setPreview(null);
    setError(null);
    if (f) {
      // Heuristic: filename-based default for the checkbox
      const lower = f.name.toLowerCase();
      const isJsonl = lower.endsWith('.jsonl') || lower.endsWith('.ndjson');
      setForceOllama(!isJsonl ? false : false); // start OFF; user can flip
      void runPreview(f, false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  }

  async function onCreate() {
    if (!file || !name) return;
    setSubmitting(true);
    setError(null);
    setStatus(
      preview?.conversion === 'ollama'
        ? 'Converting via Ollama (this can take a minute)…'
        : 'Writing dataset…',
    );
    try {
      const fd = new FormData();
      fd.append('name', name);
      fd.append('file', file);
      fd.append('force_ollama', String(forceOllama));
      const r = await fetch(`${API_URL}/api/v1/ingest/file`, {
        method: 'POST',
        body: fd,
      });
      if (!r.ok) {
        const detail = await safeError(r);
        throw new Error(detail);
      }
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
  const canSubmit =
    !!file && !!name && nameValid && !submitting && !previewing && !!preview;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Dataset</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Upload any file (jsonl, json, csv, txt, md). SLM-Forge will detect the
          format and, if needed, auto-convert it via Ollama into chat-style
          training records.
        </p>
      </div>

      {error && (
        <div className="rounded-xl bg-rose-950/50 px-3 py-2 font-mono text-xs text-rose-300">
          {error}
        </div>
      )}

      <Field label="Dataset name (lowercase, hyphens/underscores; will be the folder name)">
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

      <Field label="File (any format — jsonl / json / csv / txt / md)">
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

      {file && (
        <Field label="Conversion">
          <label className="flex items-center gap-2 text-sm text-zinc-300">
            <input
              type="checkbox"
              checked={forceOllama}
              onChange={(e) => {
                setForceOllama(e.target.checked);
                if (file) void runPreview(file, e.target.checked);
              }}
              className="h-4 w-4 rounded border-zinc-700 bg-zinc-900 accent-emerald-500"
            />
            <span>Force auto-convert via Ollama</span>
          </label>
          <p className="mt-1 text-xs text-zinc-500">
            On for messy text. Off when the file is already a known chat /
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
