import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { API_URL } from '../lib/api';

// ─── Types ─────────────────────────────────────────────────────────

type AgentMeta = {
  name: string;
  title: string;
  blurb: string;
  inputs: string[];
};

type AgentEvent = {
  stage?: string;
  ts?: string;
  result?: unknown;
  recommendation?: Record<string, unknown>;
  steps?: Record<string, unknown>;
  message?: string;
  // any other arbitrary fields
  [k: string]: unknown;
};

type Dataset = { name: string };
type ExperimentRow = { id: number; name: string };
type RunRow = { id: number; dataset: string; status: string };

// ─── Page ──────────────────────────────────────────────────────────

export default function Agents() {
  const [catalogue, setCatalogue] = useState<AgentMeta[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [experiments, setExperiments] = useState<ExperimentRow[]>([]);
  const [failedRuns, setFailedRuns] = useState<RunRow[]>([]);

  // Per-agent input state
  const [dataset, setDataset] = useState('');
  const [taskDesc, setTaskDesc] = useState('');
  const [targetDevice, setTargetDevice] = useState('mac_desktop');
  const [sessionId, setSessionId] = useState<number | ''>('');
  const [runId, setRunId] = useState<number | ''>('');

  // Live run state
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [complete, setComplete] = useState<AgentEvent | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch(`${API_URL}/api/v1/agents/`);
        if (r.ok) setCatalogue(await r.json());
      } catch (e) {
        console.error(e);
      }
      try {
        const r = await fetch(`${API_URL}/api/v1/datasets`);
        if (r.ok) setDatasets(await r.json());
      } catch (e) {
        console.error(e);
      }
      try {
        const r = await fetch(`${API_URL}/api/v1/sessions`);
        if (r.ok) setExperiments(await r.json());
      } catch (e) {
        console.error(e);
      }
      try {
        const r = await fetch(`${API_URL}/api/v1/runs?status=failed&limit=20`);
        if (r.ok) setFailedRuns(await r.json());
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  const closeStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  useEffect(() => closeStream, [closeStream]);

  function reset() {
    closeStream();
    setEvents([]);
    setComplete(null);
    setError(null);
  }

  function pickAgent(name: string) {
    reset();
    setRunning(false);
    setActive(name);
    if (name === 'experiment_recommender' && !dataset && datasets[0]) {
      setDataset(datasets[0].name);
    }
    if (name === 'evaluation_designer' && !dataset && datasets[0]) {
      setDataset(datasets[0].name);
    }
    if (name === 'optimization_coach' && sessionId === '' && experiments[0]) {
      setSessionId(experiments[0].id);
    }
    if (name === 'incident_responder' && runId === '' && failedRuns[0]) {
      setRunId(failedRuns[0].id);
    }
  }

  function buildPayload(): Record<string, unknown> | null {
    if (active === 'experiment_recommender') {
      if (!dataset || !taskDesc.trim()) {
        setError('Pick a dataset and describe the task.');
        return null;
      }
      return { dataset, task_description: taskDesc, target_device: targetDevice };
    }
    if (active === 'optimization_coach') {
      if (sessionId === '') {
        setError('Pick a session.');
        return null;
      }
      return { session_id: sessionId };
    }
    if (active === 'evaluation_designer') {
      if (!dataset) {
        setError('Pick a dataset.');
        return null;
      }
      return { dataset };
    }
    if (active === 'incident_responder') {
      if (runId === '') {
        setError('Pick a failed run.');
        return null;
      }
      return { run_id: runId };
    }
    return null;
  }

  async function run() {
    if (!active) return;
    const payload = buildPayload();
    if (!payload) return;
    reset();
    setRunning(true);
    try {
      // POST first to validate inputs and kick off the stream — then connect SSE.
      // The /run endpoint itself returns the SSE response, so we POST via fetch
      // and read it incrementally. EventSource doesn't support POST so we
      // hand-roll a reader.
      const r = await fetch(`${API_URL}/api/v1/agents/${active}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error(j.detail || `HTTP ${r.status}`);
      }
      if (!r.body) throw new Error('No response body');
      const reader = r.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        // SSE frames are separated by blank lines.
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          handleFrame(frame);
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  function handleFrame(frame: string) {
    let event = '';
    let data = '';
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) return;
    try {
      const parsed = JSON.parse(data) as AgentEvent;
      if (event === 'complete') {
        setComplete(parsed);
      } else if (event === 'error') {
        setError(parsed.message ?? 'Agent reported an error');
      } else {
        setEvents((prev) => [...prev, { stage: event, ...parsed }]);
      }
    } catch {
      /* ignore unparseable frames */
    }
  }

  const meta = catalogue.find((a) => a.name === active);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="mt-1 text-sm text-hcl-dark/50">
          Multi-step Hermes workflows. Each agent chains 2-4 skills into a
          single end-to-end recommendation.
        </p>
      </div>

      {catalogue.length === 0 ? (
        <div className="text-sm text-hcl-dark/50">Loading agents…</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {catalogue.map((a) => (
            <button
              key={a.name}
              type="button"
              onClick={() => pickAgent(a.name)}
              className={`rounded-xl border p-4 text-left transition-colors ${
                active === a.name
                  ? 'border-hcl-teal/30 bg-hcl-teal/10'
                  : 'border-hcl-light-blue bg-white hover:border-hcl-teal/30'
              }`}
            >
              <h3 className="font-medium text-hcl-dark">{a.title}</h3>
              <p className="mt-1 text-xs text-hcl-dark/60">{a.blurb}</p>
              <p className="mt-2 font-mono text-[10px] text-hcl-dark/50">
                inputs: {a.inputs.join(', ')}
              </p>
            </button>
          ))}
        </div>
      )}

      {active && (
        <section className="rounded-xl border border-hcl-light-blue bg-white p-4 space-y-4">
          <h2 className="text-sm font-medium text-hcl-dark">
            {meta?.title ?? active}
          </h2>

          {/* Input fields per agent */}
          {(active === 'experiment_recommender' || active === 'evaluation_designer') && (
            <Field label="Dataset">
              <select
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              >
                <option value="">— pick a dataset —</option>
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {active === 'experiment_recommender' && (
            <>
              <Field label="Task description">
                <textarea
                  value={taskDesc}
                  onChange={(e) => setTaskDesc(e.target.value)}
                  rows={2}
                  placeholder="e.g. Stock-analyst Q&A in a terse, factual tone for iPhone deployment."
                  className="w-full resize-none rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 text-sm placeholder:text-hcl-dark/50"
                />
              </Field>
              <Field label="Target device">
                <select
                  value={targetDevice}
                  onChange={(e) => setTargetDevice(e.target.value)}
                  className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
                >
                  <option value="mac_desktop">mac_desktop</option>
                  <option value="mac_laptop">mac_laptop</option>
                  <option value="iphone_pro">iphone_pro</option>
                  <option value="iphone_base">iphone_base</option>
                  <option value="ipad">ipad</option>
                </select>
              </Field>
            </>
          )}

          {active === 'optimization_coach' && (
            <Field label="Experiment / session">
              <select
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value ? Number(e.target.value) : '')}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              >
                <option value="">— pick a session —</option>
                {experiments.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} — {s.name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {active === 'incident_responder' && (
            <Field label="Failed run">
              <select
                value={runId}
                onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : '')}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              >
                <option value="">— pick a failed run —</option>
                {failedRuns.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} — {r.dataset}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {error && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </div>
          )}

          <div>
            <button
              type="button"
              onClick={() => void run()}
              disabled={running}
              className="rounded-md bg-hcl-dark-teal px-4 py-2 text-sm font-medium text-white hover:bg-hcl-teal disabled:cursor-not-allowed disabled:bg-hcl-light-blue"
            >
              {running ? 'Running…' : 'Run agent'}
            </button>
          </div>

          {/* Live event stream */}
          {events.length > 0 && (
            <div className="rounded-md border border-hcl-light-blue bg-hcl-bg p-3 space-y-1">
              <h3 className="text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
                Progress
              </h3>
              <ol className="space-y-1 font-mono text-[11px] text-hcl-dark/60">
                {events.map((ev, i) => (
                  <li key={i}>
                    <span className="text-hcl-teal">●</span>{' '}
                    <span className="text-hcl-dark">{ev.stage}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Final recommendation */}
          {complete && (
            <div className="rounded-md border border-hcl-teal/30 bg-hcl-teal/5 p-4 text-xs space-y-2">
              <h3 className="font-medium uppercase tracking-wider text-hcl-teal">
                ✓ Final recommendation
              </h3>
              <Recommendation
                agent={active}
                recommendation={
                  (complete.recommendation as Record<string, unknown>) ?? {}
                }
              />
              <details className="mt-2">
                <summary className="cursor-pointer text-hcl-dark/50">
                  Raw steps
                </summary>
                <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-hcl-dark/60">
                  {JSON.stringify(complete.steps, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

// ─── Subviews ──────────────────────────────────────────────────────

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

function Recommendation({
  agent,
  recommendation,
}: {
  agent: string | null;
  recommendation: Record<string, unknown>;
}) {
  if (agent === 'experiment_recommender') {
    return (
      <div className="space-y-1 font-mono">
        <Row label="ready_to_train" value={String(recommendation.ready_to_train ?? '?')} />
        <Row label="base_model" value={String(recommendation.base_model ?? '?')} />
        <Row label="method" value={String(recommendation.method ?? '?')} />
        <Row label="num_layers" value={String(recommendation.num_layers ?? '?')} />
        <Row label="learning_rate" value={String(recommendation.learning_rate ?? '?')} />
        <Row label="iters" value={String(recommendation.iters ?? '?')} />
        {typeof recommendation.rationale === 'string' && (
          <p className="mt-2 italic text-hcl-dark/80">{recommendation.rationale}</p>
        )}
        <p className="mt-2 text-hcl-dark/50">
          Apply manually on{' '}
          <Link to="/experiments/new" className="text-hcl-teal underline">
            New Experiment
          </Link>
          .
        </p>
      </div>
    );
  }
  if (agent === 'optimization_coach') {
    const decision = String(recommendation.decision ?? 'continue');
    const tone =
      decision === 'stop' ? 'rose' : decision === 'pivot' ? 'amber' : 'emerald';
    return (
      <div className="space-y-2">
        <div className="text-sm">
          Decision:{' '}
          <span
            className={`font-mono uppercase ${
              tone === 'rose'
                ? 'text-red-600'
                : tone === 'amber'
                ? 'text-hcl-warning'
                : 'text-hcl-teal'
            }`}
          >
            {decision}
          </span>
        </div>
        {typeof recommendation.reason === 'string' && (
          <p className="italic text-hcl-dark/80">{recommendation.reason}</p>
        )}
        {recommendation.max_observed_drift != null && (
          <div className="font-mono text-hcl-dark/60">
            max drift {String(recommendation.max_observed_drift)} / threshold{' '}
            {String(recommendation.drift_threshold)}
          </div>
        )}
      </div>
    );
  }
  if (agent === 'evaluation_designer') {
    const canary = (recommendation.canary_set as unknown[]) ?? [];
    const criteria = (recommendation.success_criteria as unknown[]) ?? [];
    const benchmarks = (recommendation.benchmark_questions as unknown[]) ?? [];
    return (
      <div className="space-y-3">
        <div>
          <h4 className="text-hcl-dark/80">Canary set ({canary.length} records)</h4>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-hcl-dark/60">
            {JSON.stringify(canary, null, 2)}
          </pre>
        </div>
        {criteria.length > 0 && (
          <div>
            <h4 className="text-hcl-dark/80">Success criteria</h4>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-[10px] text-hcl-dark/60">
              {JSON.stringify(criteria, null, 2)}
            </pre>
          </div>
        )}
        {benchmarks.length > 0 && (
          <div>
            <h4 className="text-hcl-dark/80">Benchmark questions</h4>
            <ol className="mt-1 list-decimal pl-5 text-hcl-dark/80">
              {benchmarks.map((q, i) => (
                <li key={i}>{String(q)}</li>
              ))}
            </ol>
          </div>
        )}
      </div>
    );
  }
  if (agent === 'incident_responder') {
    const md = String(recommendation.post_mortem_markdown ?? '');
    return (
      <div className="space-y-2">
        <div>
          Root cause:{' '}
          <span className="font-mono text-red-600">
            {String(recommendation.root_cause ?? 'unknown')}
          </span>
          {' · '}
          rerun safe:{' '}
          <span className="font-mono">
            {String(recommendation.rerun_safe ?? false)}
          </span>
        </div>
        {md && (
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
            {md}
          </pre>
        )}
      </div>
    );
  }
  return (
    <pre className="whitespace-pre-wrap font-mono text-[11px] text-hcl-dark/80">
      {JSON.stringify(recommendation, null, 2)}
    </pre>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="w-32 text-hcl-dark/50">{label}</span>
      <span className="text-hcl-dark">{value}</span>
    </div>
  );
}
