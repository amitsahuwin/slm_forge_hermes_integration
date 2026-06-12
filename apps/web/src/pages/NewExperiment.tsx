import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import HermesSkillButton, {
  type SkillResponse,
} from '../components/HermesSkillButton';
import { API_URL, type BaseModelInfo, type DatasetInfo, type RunMethod, api } from '../lib/api';

type HermesMethodSuggestion = {
  method?: 'lora' | 'dora' | 'full';
  num_layers?: number;
  learning_rate?: number;
  batch_size?: number;
  iters?: number;
  reasoning?: string;
};

type ModelRec = {
  primary?: string;
  alternatives?: string[];
  reasoning?: string;
  expected_iphone_size_gb?: number;
};

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

  // ── Ask Hermes (Phase N.1) ─────────────────────────────────
  const [taskDescription, setTaskDescription] = useState('');
  const [askingHermes, setAskingHermes] = useState(false);
  const [suggestion, setSuggestion] = useState<HermesMethodSuggestion | null>(null);
  const [suggestionRaw, setSuggestionRaw] = useState<string | null>(null);
  const [suggestionError, setSuggestionError] = useState<string | null>(null);

  async function askHermes() {
    if (!taskDescription.trim()) {
      setSuggestionError('Describe the task first (e.g. "stock-analyst Q&A in a curt, factual tone").');
      return;
    }
    setAskingHermes(true);
    setSuggestionError(null);
    setSuggestion(null);
    setSuggestionRaw(null);
    try {
      const ds = datasets.find((d) => d.name === dataset);
      const r = await fetch(`${API_URL}/api/v1/hermes/select-method`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_description: taskDescription,
          base_model: baseModel,
          dataset_name: dataset || undefined,
          n_train_examples: ds ? ds.train_count : undefined,
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      const data = (await r.json()) as {
        parsed: HermesMethodSuggestion | null;
        raw: string;
      };
      setSuggestion(data.parsed);
      setSuggestionRaw(data.raw);
    } catch (e: unknown) {
      setSuggestionError(e instanceof Error ? e.message : String(e));
    } finally {
      setAskingHermes(false);
    }
  }

  function applySuggestion() {
    if (!suggestion) return;
    if (suggestion.method) setMethod(suggestion.method);
    if (suggestion.num_layers) setNumLayers(suggestion.num_layers);
    if (suggestion.learning_rate) setLearningRate(suggestion.learning_rate);
    if (suggestion.iters) setIters(suggestion.iters);
  }

  // Phase N.4 — model_selection (separate from method selection)
  const [modelRec, setModelRec] = useState<ModelRec | null>(null);
  const [modelRecRaw, setModelRecRaw] = useState<string | null>(null);

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

      {/* Phase N.1 — Ask Hermes to recommend a method + hyperparams */}
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-zinc-200">
            💡 Ask Hermes for a method recommendation
          </h2>
          {suggestion && (
            <button
              type="button"
              onClick={applySuggestion}
              className="rounded border border-emerald-700 bg-emerald-900/40 px-2.5 py-1 text-xs text-emerald-200 hover:bg-emerald-800/50"
            >
              Apply to form ↓
            </button>
          )}
        </div>
        <p className="text-xs text-zinc-500">
          Describe the task in plain English. Hermes picks LoRA / DoRA / full SFT
          and proposes baseline hyperparams using the
          <code className="ml-1 rounded bg-zinc-800 px-1 py-0.5 font-mono text-[10px] text-zinc-300">
            select_method_for_task
          </code>{' '}
          skill.
        </p>
        <textarea
          value={taskDescription}
          onChange={(e) => setTaskDescription(e.target.value)}
          rows={2}
          placeholder="e.g. Stock-analyst Q&A in a curt, factual tone. Sub-3B base model. ~20 examples."
          className="w-full resize-none rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-600 focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={askHermes}
            disabled={askingHermes || !taskDescription.trim()}
            className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {askingHermes ? 'Asking…' : 'Recommend method (LoRA/DoRA/SFT)'}
          </button>
          <HermesSkillButton
            path="/api/v1/hermes/model-selection"
            body={{
              task_description: taskDescription,
              dataset_name: dataset || undefined,
              n_train_examples: datasets.find((d) => d.name === dataset)?.train_count,
              target_device: 'mac_desktop',
            }}
            label="Pick base model"
            emoji="🧬"
            tone="zinc"
            disabled={!taskDescription.trim()}
            onResult={(r: SkillResponse) => {
              setModelRec((r.parsed as ModelRec) ?? null);
              setModelRecRaw(r.parsed ? null : r.raw);
            }}
            onClear={() => {
              setModelRec(null);
              setModelRecRaw(null);
            }}
          />
          {suggestionError && (
            <span className="text-xs text-rose-400">{suggestionError}</span>
          )}
        </div>

        {(modelRec || modelRecRaw) && (
          <div className="rounded-md bg-zinc-950 px-3 py-2 text-xs text-zinc-300 space-y-1">
            {modelRec ? (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <span className="text-zinc-500">Primary:</span>{' '}
                    <span className="font-mono text-emerald-300">{modelRec.primary}</span>
                    {modelRec.expected_iphone_size_gb != null && (
                      <span className="ml-2 text-zinc-500">
                        ({modelRec.expected_iphone_size_gb.toFixed(1)} GB on iPhone)
                      </span>
                    )}
                  </div>
                  {modelRec.primary && (
                    <button
                      type="button"
                      onClick={() => setBaseModel(modelRec.primary!)}
                      className="rounded border border-emerald-700 bg-emerald-900/40 px-2 py-0.5 text-emerald-200 hover:bg-emerald-800/50"
                    >
                      Use it ↓
                    </button>
                  )}
                </div>
                {modelRec.alternatives?.length ? (
                  <div className="text-zinc-500">
                    Alternatives:{' '}
                    {modelRec.alternatives.map((alt, i) => (
                      <span key={alt}>
                        <button
                          type="button"
                          onClick={() => setBaseModel(alt)}
                          className="font-mono text-zinc-300 underline-offset-2 hover:text-emerald-300 hover:underline"
                        >
                          {alt}
                        </button>
                        {i < modelRec.alternatives!.length - 1 ? ', ' : ''}
                      </span>
                    ))}
                  </div>
                ) : null}
                {modelRec.reasoning && (
                  <p className="italic text-zinc-400">{modelRec.reasoning}</p>
                )}
              </>
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-[11px] text-zinc-400">
                {modelRecRaw}
              </pre>
            )}
          </div>
        )}
        {(suggestion || suggestionRaw) && (
          <div className="rounded-md bg-zinc-950 px-3 py-2 text-xs text-zinc-300">
            {suggestion ? (
              <>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono">
                  {suggestion.method && (
                    <Row label="method" value={suggestion.method} />
                  )}
                  {suggestion.num_layers != null && (
                    <Row label="num_layers" value={String(suggestion.num_layers)} />
                  )}
                  {suggestion.learning_rate != null && (
                    <Row
                      label="learning_rate"
                      value={suggestion.learning_rate.toExponential(2)}
                    />
                  )}
                  {suggestion.batch_size != null && (
                    <Row label="batch_size" value={String(suggestion.batch_size)} />
                  )}
                  {suggestion.iters != null && (
                    <Row label="iters" value={String(suggestion.iters)} />
                  )}
                </div>
                {suggestion.reasoning && (
                  <p className="mt-2 italic text-zinc-400">{suggestion.reasoning}</p>
                )}
              </>
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-[11px] text-zinc-400">
                {suggestionRaw}
              </pre>
            )}
          </div>
        )}
      </section>

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

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-zinc-500">{label}</span>
      <span className="text-zinc-100">{value}</span>
    </>
  );
}

function Num({
  value,
  onChange,
  ...rest
}: { value: number; onChange: (n: number) => void } & Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  'value' | 'onChange'
>) {
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
