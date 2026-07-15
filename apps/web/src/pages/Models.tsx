/**
 * Models tab — the dynamic model registry.
 *
 * Browse the effective catalog (built-in seeds + downloaded models) and
 * register a new one by pasting a HuggingFace repo id. The download is a
 * register+validate job: it validates the repo via the HF Hub API and
 * persists a global catalog entry — weights are still fetched by the
 * trainer worker at train time, so the training path is untouched.
 *
 * The catalog feed here (`/api/v1/models/v2`) is the same one the New Run
 * and New Experiment dropdowns consume, so a downloaded model shows up
 * everywhere automatically.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  type CatalogModelV2,
  type RegistryEntry,
  type TrainerBackendName,
  models as modelsApi,
} from '../lib/api';
import { toast } from '../lib/toast';
import { useCan } from '../auth/useCan';

const STATUS_STYLES: Record<string, string> = {
  stable: 'border-emerald-800 bg-emerald-950/40 text-emerald-200',
  untested: 'border-amber-800 bg-amber-950/40 text-amber-200',
  broken: 'border-rose-800 bg-rose-950/40 text-rose-200',
};

export default function Models() {
  const navigate = useNavigate();
  const canDownload = useCan('create', 'model');
  const canDelete = useCan('delete', 'model');

  const [catalog, setCatalog] = useState<CatalogModelV2[]>([]);
  const [registry, setRegistry] = useState<RegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [hfId, setHfId] = useState('');
  const [backend, setBackend] = useState<'' | TrainerBackendName>('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [cat, reg] = await Promise.all([
        modelsApi.listCatalog(),
        modelsApi.listRegistry(),
      ]);
      setCatalog(cat);
      setRegistry(reg);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Keys present in the registry mark a catalog row as "downloaded" (vs a
  // built-in seed). O(1) membership checks while rendering.
  const registeredKeys = useMemo(
    () => new Set(registry.map((r) => r.key)),
    [registry],
  );

  const submit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    const id = hfId.trim();
    if (!id) return;
    setSubmitting(true);
    try {
      const res = await modelsApi.download(id, backend || undefined);
      toast.success(`Queued ${res.hf_id} (${res.target_backend})`);
      navigate(`/jobs?id=${encodeURIComponent(res.job_id)}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (key: string, label: string) => {
    if (!window.confirm(`Remove "${label}" from the registry?`)) return;
    try {
      await modelsApi.deleteRegistered(key);
      toast.success(`Removed ${label}`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-zinc-100">Models</h1>
        <p className="mt-1 text-sm text-zinc-400">
          The model registry. Downloaded models appear in New Run and New
          Experiment automatically. Registering validates the HuggingFace repo
          and records its metadata — weights download on the worker at train
          time.
        </p>
      </header>

      {/* ─── Download form ─────────────────────────────────────── */}
      {canDownload && (
        <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-5">
          <div className="mb-3 text-xs uppercase tracking-wide text-zinc-500">
            Add a model
          </div>
          <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
            <label className="flex-1 min-w-[280px]">
              <span className="mb-1 block text-xs text-zinc-400">
                HuggingFace model id
              </span>
              <input
                type="text"
                value={hfId}
                onChange={(e) => setHfId(e.target.value)}
                placeholder="Qwen/Qwen2.5-1.5B-Instruct"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-700 focus:outline-none"
                autoFocus
              />
            </label>
            <label>
              <span className="mb-1 block text-xs text-zinc-400">Backend</span>
              <select
                value={backend}
                onChange={(e) =>
                  setBackend(e.target.value as '' | TrainerBackendName)
                }
                className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-700 focus:outline-none"
              >
                <option value="">Auto-detect</option>
                <option value="mlx">MLX (Apple Silicon)</option>
                <option value="cuda">CUDA (NVIDIA)</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={submitting || !hfId.trim()}
              className="rounded-md border border-emerald-800 bg-emerald-950/40 px-4 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-900/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? 'Queuing…' : 'Download'}
            </button>
          </form>
          <p className="mt-2 text-xs text-zinc-500">
            e.g.{' '}
            <code className="text-zinc-400">meta-llama/Llama-3.2-1B-Instruct</code>,{' '}
            <code className="text-zinc-400">Qwen/Qwen3-1.7B</code>,{' '}
            <code className="text-zinc-400">google/gemma-3-1b-it</code>. Gated
            repos require an accepted license + <code>HF_TOKEN</code>.
          </p>
        </section>
      )}

      {/* ─── Catalog ───────────────────────────────────────────── */}
      <section>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-xs uppercase tracking-wide text-zinc-500">
            Available models
          </div>
          <button
            onClick={() => void load()}
            className="text-xs text-zinc-400 hover:text-zinc-200"
          >
            ↻ Refresh
          </button>
        </div>

        {loadError && (
          <div className="rounded-md border border-rose-800 bg-rose-950/30 p-4 text-sm text-rose-200">
            {loadError}
          </div>
        )}

        {loading && !catalog.length ? (
          <div className="text-sm text-zinc-500">Loading…</div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {catalog.map((m) => {
              const downloaded = registeredKeys.has(m.key);
              const backends = Object.entries(m.backends) as [
                TrainerBackendName,
                CatalogModelV2['backends'][TrainerBackendName],
              ][];
              return (
                <div
                  key={m.key}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-zinc-100">
                          {m.label}
                        </span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                            downloaded
                              ? 'border-sky-700 bg-sky-500/10 text-sky-300'
                              : 'border-zinc-700 bg-zinc-800/60 text-zinc-400'
                          }`}
                        >
                          {downloaded ? 'downloaded' : 'built-in'}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-zinc-500">
                        {m.family} · {m.size_params} · {m.recommended_method}
                      </div>
                    </div>
                    {canDelete && downloaded && (
                      <button
                        onClick={() => void remove(m.key, m.label)}
                        className="rounded-md border border-rose-900 bg-rose-950/30 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/40"
                      >
                        Remove
                      </button>
                    )}
                  </div>

                  <div className="mt-3 space-y-1.5">
                    {backends.map(([bk, v]) =>
                      v ? (
                        <div
                          key={bk}
                          className="flex items-center gap-2 text-xs"
                        >
                          <span className="rounded border border-zinc-700 bg-zinc-800/60 px-1.5 py-0.5 font-mono uppercase text-zinc-300">
                            {bk}
                          </span>
                          <span className="font-mono text-zinc-400">
                            {v.model_id}
                          </span>
                          <span
                            className={`rounded border px-1.5 py-0.5 ${
                              STATUS_STYLES[v.status] ??
                              'border-zinc-700 bg-zinc-800/60 text-zinc-300'
                            }`}
                          >
                            {v.status}
                          </span>
                          {v.gated && (
                            <span className="rounded border border-amber-800 bg-amber-950/40 px-1.5 py-0.5 text-amber-200">
                              gated
                            </span>
                          )}
                          <span className="text-zinc-600">
                            {v.min_memory_gb} GB
                          </span>
                        </div>
                      ) : null,
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}