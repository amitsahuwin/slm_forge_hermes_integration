import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import HermesSkillButton, {
  type SkillResponse,
} from '../components/HermesSkillButton';
import {
  API_URL,
  type CatalogModelV2,
  type DatasetInfo,
  type RunMethod,
  type TrainerBackendName,
  api,
} from '../lib/api';
import { BACKEND_OPTIONS, defaultModelId } from '../lib/backends';

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
  const [models, setModels] = useState<CatalogModelV2[]>([]);
  const [backend, setBackend] = useState<TrainerBackendName | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [name, setName] = useState('stock-analyst-sweep');
  const [dataset, setDataset] = useState('');
  const [baseModel, setBaseModel] = useState('');
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
    // Phase T/U: detect the platform backend, then drive the model dropdown
    // from the backend-aware v2 catalog (parity with NewRun).
    Promise.all([api.getPlatformInfo(), api.listDatasets(), api.listModelsV2()])
      .then(([plat, ds, ms]) => {
        setDatasets(ds);
        setModels(ms);
        if (ds.length > 0) setDataset(ds[0].name);

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
      const s = await api.createSession({
        name,
        dataset,
        base_model: baseModel,
        trainer_backend: backend ?? undefined,
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
      <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{loadError}</div>
    );
  }

  const backendTip = BACKEND_OPTIONS.find((b) => b.value === backend)?.tip ?? '';

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Experiment</h1>
        <p className="mt-1 text-sm text-hcl-dark/50">
          Hermes will autonomously sweep hyperparameters across multiple rounds. Make sure
          <code className="ml-1 rounded bg-hcl-tech-grey px-1.5 py-0.5 text-xs">make trainer</code> and
          <code className="ml-1 rounded bg-hcl-tech-grey px-1.5 py-0.5 text-xs">make ratchet</code> are
          both running.
        </p>
      </div>

      {/* Phase N.1 — Ask Hermes to recommend a method + hyperparams */}
      <section className="rounded-xl border border-hcl-light-blue bg-white p-4 space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-hcl-dark">
            💡 Ask Hermes for a method recommendation
          </h2>
          {suggestion && (
            <button
              type="button"
              onClick={applySuggestion}
              className="rounded border border-hcl-teal/30 bg-hcl-teal/10 px-2.5 py-1 text-xs text-hcl-dark-teal hover:bg-hcl-dark-teal/30"
            >
              Apply to form ↓
            </button>
          )}
        </div>
        <p className="text-xs text-hcl-dark/50">
          Describe the task in plain English. Hermes picks LoRA / DoRA / full SFT
          and proposes baseline hyperparams using the
          <code className="ml-1 rounded bg-hcl-tech-grey px-1 py-0.5 font-mono text-[10px] text-hcl-dark/80">
            select_method_for_task
          </code>{' '}
          skill.
        </p>
        <textarea
          value={taskDescription}
          onChange={(e) => setTaskDescription(e.target.value)}
          rows={2}
          placeholder="e.g. Stock-analyst Q&A in a curt, factual tone. Sub-3B base model. ~20 examples."
          className="w-full resize-none rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 text-sm text-hcl-dark placeholder:text-hcl-dark/50 focus:border-hcl-teal focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={askHermes}
            disabled={askingHermes || !taskDescription.trim()}
            className="rounded-md border border-hcl-teal/30 px-3 py-1.5 text-xs text-hcl-dark hover:bg-hcl-tech-grey disabled:cursor-not-allowed disabled:opacity-50"
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
            <span className="text-xs text-red-600">{suggestionError}</span>
          )}
        </div>

        {(modelRec || modelRecRaw) && (
          <div className="rounded-md bg-hcl-bg px-3 py-2 text-xs text-hcl-dark/80 space-y-1">
            {modelRec ? (
              <>
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <span className="text-hcl-dark/50">Primary:</span>{' '}
                    <span className="font-mono text-hcl-teal">{modelRec.primary}</span>
                    {modelRec.expected_iphone_size_gb != null && (
                      <span className="ml-2 text-hcl-dark/50">
                        ({modelRec.expected_iphone_size_gb.toFixed(1)} GB on iPhone)
                      </span>
                    )}
                  </div>
                  {modelRec.primary && (
                    <button
                      type="button"
                      onClick={() => setBaseModel(modelRec.primary!)}
                      className="rounded border border-hcl-teal/30 bg-hcl-teal/10 px-2 py-0.5 text-hcl-dark-teal hover:bg-hcl-dark-teal/30"
                    >
                      Use it ↓
                    </button>
                  )}
                </div>
                {modelRec.alternatives?.length ? (
                  <div className="text-hcl-dark/50">
                    Alternatives:{' '}
                    {modelRec.alternatives.map((alt, i) => (
                      <span key={alt}>
                        <button
                          type="button"
                          onClick={() => setBaseModel(alt)}
                          className="font-mono text-hcl-dark/80 underline-offset-2 hover:text-hcl-teal hover:underline"
                        >
                          {alt}
                        </button>
                        {i < modelRec.alternatives!.length - 1 ? ', ' : ''}
                      </span>
                    ))}
                  </div>
                ) : null}
                {modelRec.reasoning && (
                  <p className="italic text-hcl-dark/60">{modelRec.reasoning}</p>
                )}
              </>
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/60">
                {modelRecRaw}
              </pre>
            )}
          </div>
        )}
        {(suggestion || suggestionRaw) && (
          <div className="rounded-md bg-hcl-bg px-3 py-2 text-xs text-hcl-dark/80">
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
                  <p className="mt-2 italic text-hcl-dark/60">{suggestion.reasoning}</p>
                )}
              </>
            ) : (
              <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/60">
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
            className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
          />
        </Field>

        <Field label="Training backend">
          <select
            value={backend ?? ''}
            onChange={(e) => onBackendChange(e.target.value as TrainerBackendName)}
            className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
          >
            {BACKEND_OPTIONS.map((b) => (
              <option key={b.value} value={b.value}>
                {b.value} — {b.label}
              </option>
            ))}
          </select>
          {backendTip && <p className="mt-1.5 text-xs text-hcl-dark/50">{backendTip}</p>}
        </Field>

        <Field label="Dataset">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
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
            className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
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
            <div className="mt-1.5 space-y-0.5 text-xs text-hcl-dark/50">
              <p>
                <span className="font-mono text-hcl-dark/60">{selected.variant.model_id}</span>
                {' · needs ≥ '}
                {selected.variant.min_memory_gb} GB
                {' · '}
                <StatusBadge status={selected.variant.status} />
                {selected.variant.gated && (
                  <span className="ml-1.5 rounded bg-hcl-warning/10 px-1.5 py-0.5 text-[10px] font-medium uppercase text-hcl-warning">
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
              className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
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
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
            {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !dataset || !baseModel}
          className="rounded-md bg-hcl-dark-teal px-4 py-2 text-sm font-medium text-white hover:bg-hcl-teal disabled:cursor-not-allowed disabled:bg-hcl-light-blue"
        >
          {submitting ? 'Starting…' : 'Start autoresearch experiment'}
        </button>
      </form>
    </div>
  );
}

function StatusBadge({ status }: { status: 'stable' | 'untested' | 'broken' }) {
  const styles: Record<string, string> = {
    stable: 'bg-hcl-teal/10 text-hcl-teal',
    untested: 'bg-hcl-warning/10 text-hcl-warning',
    broken: 'bg-red-50 text-red-600',
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
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
        {label}
      </span>
      {children}
    </label>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-hcl-dark/50">{label}</span>
      <span className="text-hcl-dark">{value}</span>
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
      className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
      {...rest}
    />
  );
}
