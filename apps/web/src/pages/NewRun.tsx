import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

export default function NewRun() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<BaseModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('mlx-community/gemma-3n-E2B-it-bf16');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(200);
  const [batchSize, setBatchSize] = useState(4);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);

  useEffect(() => {
    Promise.all([api.listDatasets(), api.listModels()])
      .then(([ds, ms]) => {
        setDatasets(ds);
        setModels(ms);
        if (ds.length > 0) setDataset(ds[0].name);
      })
      .catch((e: unknown) => setLoadError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const run = await api.createRun({
        dataset,
        base_model: baseModel,
        method,
        iters,
        batch_size: batchSize,
        learning_rate: learningRate,
        num_layers: numLayers,
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

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Run</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Make sure <code className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make trainer</code> is
          running in another terminal so the job actually starts.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
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
            {models.map((m) => (
              <option key={m.hf_id} value={m.hf_id}>
                {m.label}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-zinc-500">
            {models.find((m) => m.hf_id === baseModel)?.notes ?? ''}
          </p>
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
        </div>

        {submitError && (
          <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset}
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-zinc-700"
        >
          {submitting ? 'Starting…' : 'Start training'}
        </button>
      </form>
    </div>
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
} & React.InputHTMLAttributes<HTMLInputElement>) {
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
