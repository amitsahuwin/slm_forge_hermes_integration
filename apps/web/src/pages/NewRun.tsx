import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  type CatalogModelV2,
  type DatasetInfo,
  type RunMethod,
  type TrainerBackendName,
  api,
} from '../lib/api';
import { BACKEND_OPTIONS, defaultModelId } from '../lib/backends';

export default function NewRun() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<CatalogModelV2[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [dataset, setDataset] = useState('');
  const [backend, setBackend] = useState<TrainerBackendName | null>(null);
  const [baseModel, setBaseModel] = useState('');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(200);
  const [batchSize, setBatchSize] = useState(4);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);
  // Default ON — long-sequence datasets OOM Apple Silicon without it.
  const [gradCheckpoint, setGradCheckpoint] = useState(true);

  useEffect(() => {
    // Phase T: Fetch platform info first to set smart defaults
    Promise.all([api.getPlatformInfo(), api.listDatasets(), api.listModelsV2()])
      .then(([plat, ds, ms]) => {
        setDatasets(ds);
        setModels(ms);
        if (ds.length > 0) setDataset(ds[0].name);
        // Use platform-detected default backend
        const defaultBackend = plat.default_backend;
        setBackend(defaultBackend);
        setBaseModel(defaultModelId(ms, defaultBackend));
      })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  /** Models that have a variant for the selected backend. */
  const backendModels = useMemo(
    () => (backend ? models.filter((m) => m.backends[backend] !== undefined) : []),
    [models, backend],
  );

  const selected = useMemo(() => {
    if (!backend) return null;
    for (const m of backendModels) {
      const v = m.backends[backend];
      if (v && v.model_id === baseModel) return { model: m, variant: v };
    }
    return null;
  }, [backendModels, backend, baseModel]);

  function onBackendChange(next: TrainerBackendName) {
    // Keep the same logical model across backends when possible.
    const current = models.find((m) =>
      Object.values(m.backends).some((v) => v?.model_id === baseModel),
    );
    const mapped = current?.backends[next];
    setBackend(next);
    setBaseModel(
      mapped && mapped.status !== 'broken' ? mapped.model_id : defaultModelId(models, next),
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await api.createRun({
        dataset,
        base_model: baseModel,
        trainer_backend: backend ?? undefined,
        method,
        iters,
        batch_size: batchSize,
        learning_rate: learningRate,
        num_layers: numLayers,
        grad_checkpoint: gradCheckpoint,
      });
      navigate(`/runs/${run.id}`);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  if (loadError) {
    return <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{loadError}</div>;
  }

  const backendTip = BACKEND_OPTIONS.find((b) => b.value === backend)?.tip ?? '';

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>
        <p className="mt-1 text-sm text-zinc-500">{backendTip}</p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="Training backend">
          <select
            value={backend ?? ''}
            onChange={(e) => onBackendChange(e.target.value as TrainerBackendName)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {BACKEND_OPTIONS.map((b) => (
              <option key={b.value} value={b.value}>
                {b.value} — {b.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {datasets.length === 0 ? (
              <option value="">— none found; run `make seed-data` —</option>
            ) : (
              datasets.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}  ({d.train_count} train / {d.valid_count} valid)
                </option>
              ))
            )}
          </select>
        </Field>

        <Field label="Base model">
          <select
            value={baseModel}
            onChange={(e) => setBaseModel(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {backendModels.map((m) => {
              const v = m.backends[backend!]!;
              const broken = v.status === 'broken';
              return (
                <option key={v.model_id} value={v.model_id} disabled={broken}>
                  {m.label} ({m.size_params}){v.gated ? ' — 🔒 gated' : ''}
                  {broken ? ' — ⚠ broken' : ''}
                </option>
              );
            })}
          </select>
          {selected && (
            <div className="mt-1.5 space-y-0.5 text-xs text-zinc-500">
              <p>
                <span className="font-mono text-zinc-400">{selected.variant.model_id}</span>
                {' · needs ≥ '}
                {selected.variant.min_memory_gb} GB
                {' · '}
                <StatusBadge status={selected.variant.status} />
                {selected.variant.gated && (
                  <span className="ml-1.5 rounded bg-amber-950/60 px-1.5 py-0.5 text-[10px] font-medium uppercase text-amber-400">
                    🔒 gated
                  </span>
                )}
              </p>
              {selected.variant.notes && <p>{selected.variant.notes}</p>}
            </div>
          )}
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Method">
            <select
              value={method}
              onChange={(e) => setMethod(e.target.value as RunMethod)}
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
            >
              <option value="lora">LoRA</option>
              <option value="dora">DoRA</option>
              <option value="full">Full SFT</option>
            </select>
          </Field>
          <Field label="Iterations">
            <Number value={iters} onChange={setIters} min={10} max={5000} step={10} />
          </Field>
          <Field label="Batch size">
            <Number value={batchSize} onChange={setBatchSize} min={1} max={32} step={1} />
          </Field>
          <Field label="Learning rate">
            <Number value={learningRate} onChange={setLearningRate} step={1e-5} />
          </Field>
          <Field label="Num layers (LoRA)">
            <Number value={numLayers} onChange={setNumLayers} min={1} max={48} step={1} />
          </Field>
          <Field label="Gradient checkpointing">
            <label className="flex items-center gap-2 py-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={gradCheckpoint}
                onChange={(e) => setGradCheckpoint(e.target.checked)}
                className="h-4 w-4 accent-emerald-600"
              />
              Lower memory (recommended), ~25% slower
            </label>
          </Field>
        </div>

        {submitError && (
          <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset || !baseModel}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Starting…' : 'Start training'}
        </button>
      </form>
    </div>
  );
}

function StatusBadge({ status }: { status: 'stable' | 'untested' | 'broken' }) {
  const styles: Record<string, string> = {
    stable: 'bg-emerald-950/60 text-emerald-400',
    untested: 'bg-amber-950/60 text-amber-400',
    broken: 'bg-rose-950/60 text-rose-400',
  };
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${styles[status]}`}>
      {status}
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function Number({
  value,
  onChange,
  ...rest
}: {
  value: number;
  onChange: (n: number) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'>) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => onChange(parseFloat(e.target.value))}
      className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
      {...rest}
    />
  );
}
