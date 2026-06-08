import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

export default function NewExperiment() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [models, setModels] = useState<BaseModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [name, setName] = useState('stock-analyst-sweep');
  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('mlx-community/Qwen2.5-3B-Instruct-4bit');
  const [method, setMethod] = useState<RunMethod>('lora');
  const [iters, setIters] = useState(80);
  const [learningRate, setLearningRate] = useState(1.0e-4);
  const [numLayers, setNumLayers] = useState(16);
  const [maxRounds, setMaxRounds] = useState(6);
  const [plateauPatience, setPlateauPatience] = useState(3);
  const [minDelta, setMinDelta] = useState(0.005);

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
      const s = await api.createSession({
        name,
        dataset,
        base_model: baseModel,
        method,
        iters,
        learning_rate: learningRate,
        num_layers: numLayers,
        max_rounds: maxRounds,
        plateau_patience: plateauPatience,
        min_delta: minDelta,
      });
      navigate(`/experiments/${s.id}`);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  if (loadError) {
    return (
      <div className="rounded-md bg-rose-950/50 px-3 py-2 text-sm text-rose-300">{loadError}</div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Experiment</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Hermes will autonomously sweep hyperparameters across multiple rounds. Make sure
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make trainer</code> and
          <code className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs">make ratchet</code> are
          both running.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="Experiment name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          />
        </Field>

        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm"
          >
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name} ({d.train_count} train · {d.valid_count} valid)
              </option>
            ))}
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
          <Field label="Iters per round (baseline)">
            <Num value={iters} onChange={setIters} min={20} max={1000} step={10} />
          </Field>
          <Field label="Baseline LR">
            <Num value={learningRate} onChange={setLearningRate} step={1e-5} />
          </Field>
          <Field label="Baseline num_layers">
            <Num value={numLayers} onChange={setNumLayers} min={1} max={32} step={1} />
          </Field>
          <Field label="Max rounds">
            <Num value={maxRounds} onChange={setMaxRounds} min={2} max={20} step={1} />
          </Field>
          <Field label="Plateau patience">
            <Num
              value={plateauPatience}
              onChange={setPlateauPatience}
              min={1}
              max={10}
              step={1}
            />
          </Field>
          <Field label="Min improvement (Δ val_loss)">
            <Num value={minDelta} onChange={setMinDelta} step={0.001} />
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
          {submitting ? 'Starting…' : 'Start autoresearch experiment'}
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

function Num({
  value,
  onChange,
  ...rest
}: { value: number; onChange: (n: number) => void } & React.InputHTMLAttributes<HTMLInputElement>) {
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
