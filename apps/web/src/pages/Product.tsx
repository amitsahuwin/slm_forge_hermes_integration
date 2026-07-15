/**
 * Workstream 3 — the polished "Product" showcase tab.
 *
 * A single visual landing/about page that mirrors
 * ``docs/SLM_FORGE_PRODUCT_GUIDE.md`` but renders it as cards, gradients,
 * and feature grids instead of raw Markdown. Visible to all users — it's
 * marketing material, not sensitive ops data.
 *
 * Design choices:
 *   - No external icon library; emoji + inline SVG keeps the bundle small.
 *   - Tailwind utilities only; no custom CSS file to maintain.
 *   - Section anchors so a sidebar / table-of-contents can deep-link.
 *   - Strict colour palette: zinc base + emerald/indigo/amber/sky accents
 *     so the page reads as a single coherent product surface.
 */
import { useEffect, useMemo, useState } from 'react';

// ─── Static content ────────────────────────────────────────────────────

type TabFeature = {
  emoji: string;
  name: string;
  path: string;
  purpose: string;
  golden: string[];
  hermes: string;
  accent: string; // tailwind gradient class
  admin?: boolean;
};

const TABS: TabFeature[] = [
  {
    emoji: '🩺',
    name: 'Dashboard',
    path: '/',
    purpose: 'One-glance health of every worker + Hermes + the API.',
    golden: [
      'Open the UI',
      'Glance at the 4 worker tiles',
      'Check the Hermes status card',
      'Click a tile to see its tail log',
    ],
    hermes: 'Pings Ollama, verifies the model is pulled, surfaces fix-it hints.',
    accent: 'from-emerald-500/15 via-emerald-500/5 to-transparent',
  },
  {
    emoji: '🧪',
    name: 'Experiments',
    path: '/experiments',
    purpose: 'Autoresearch sessions: Hermes proposes, the ratchet executes, round after round.',
    golden: [
      'Click +Experiment',
      'Pick dataset + base model',
      'Click "Ask Hermes" for a method suggestion',
      'Start — the ratchet takes it from here',
    ],
    hermes:
      '"Ask Hermes" runs `select_method_for_task`. Each round mutates via `propose_hyperparam_mutation`.',
    accent: 'from-indigo-500/15 via-indigo-500/5 to-transparent',
  },
  {
    emoji: '⚙️',
    name: 'Runs',
    path: '/runs',
    purpose: 'Individual fine-tuning jobs. Standalone or spawned by an experiment.',
    golden: [
      'Click +Run',
      'Accept defaults or customise',
      'Watch live loss curves stream in',
      'Open the run row for details',
    ],
    hermes:
      '4xx errors get a plain-English remedy (PR-3). Failed runs auto-generate a post-mortem (PR-2).',
    accent: 'from-sky-500/15 via-sky-500/5 to-transparent',
  },
  {
    emoji: '🗂️',
    name: 'Models',
    path: '/models',
    purpose: 'The model registry. Download any HuggingFace repo; it appears in Runs + Experiments automatically.',
    golden: [
      'Open Models',
      'Paste a HuggingFace id (e.g. Qwen/Qwen3-1.7B)',
      'Pick a backend or leave it on Auto-detect',
      'Download — track it in the Jobs tab',
    ],
    hermes:
      'No LLM. Validates the repo via the HF Hub API + records metadata; weights download on the worker at train time.',
    accent: 'from-cyan-500/15 via-cyan-500/5 to-transparent',
  },
  {
    emoji: '📦',
    name: 'Exports',
    path: '/exports',
    purpose: 'Convert a finished fine-tune to GGUF for on-device inference.',
    golden: [
      'Pick a completed run',
      'Choose quant levels',
      'Queue export',
      'Download the .gguf when done',
    ],
    hermes: '`recommend_export_quants` picks the right quant level for your target device.',
    accent: 'from-amber-500/15 via-amber-500/5 to-transparent',
  },
  {
    emoji: '📚',
    name: 'Datasets',
    path: '/datasets',
    purpose: 'Bring data in. Upload, URL, scrape, or S3 — all converge on the same JSONL shape.',
    golden: [
      'Click +Dataset',
      'Pick a source',
      'Inspect the preview + QA warnings',
      'Finalize with train/valid/canary splits',
    ],
    hermes:
      'PR-4 runs `data_quality_review` in the background; `synthesize_style_prompt` expands a seed set.',
    accent: 'from-pink-500/15 via-pink-500/5 to-transparent',
  },
  {
    emoji: '🧹',
    name: 'Maintenance',
    path: '/maintenance',
    purpose: 'Disk hygiene: plan + execute a safe cleanup of dropped runs and orphan artifacts.',
    golden: [
      'Open Maintenance',
      'Plan cleanup (dry run)',
      'Review the list',
      'Execute if happy',
    ],
    hermes: 'Deterministic file-system tool — no LLM here.',
    accent: 'from-zinc-500/15 via-zinc-500/5 to-transparent',
  },
  {
    emoji: '💬',
    name: 'Chat',
    path: '/chat',
    purpose: 'A copilot that knows SLM-Forge. Ask in natural language; it dispatches the right tools.',
    golden: [
      'Open Chat',
      'Pick a template (or type your own)',
      'Watch tool cards stream in',
      'Drill into a metric chart',
    ],
    hermes: 'LangGraph state machine with Hermes as the only LLM call. Every visible card is a tool result.',
    accent: 'from-fuchsia-500/15 via-fuchsia-500/5 to-transparent',
  },
  {
    emoji: '🔬',
    name: 'R&D',
    path: '/research',
    purpose: 'Auto-generated market-research reports — pick your next domain bet.',
    golden: [
      'Click +New report',
      'Type a topic + depth',
      'Wait for the Markdown to render',
      'Save / share / delete',
    ],
    hermes: 'Web search (DuckDuckGo / SerpAPI / Tavily) grounds Hermes; output is structured Markdown.',
    accent: 'from-teal-500/15 via-teal-500/5 to-transparent',
  },
  {
    emoji: '🤖',
    name: 'Agents',
    path: '/agents',
    purpose: 'Multi-step Hermes agents: incident responder, evaluation designer, …',
    golden: [
      'Pick an agent kind',
      'Fill the input form',
      'Click Run',
      'Watch the per-step trace stream',
    ],
    hermes:
      'Each step is a separate Hermes skill call. Currently shipped: incident_responder, evaluation_designer.',
    accent: 'from-lime-500/15 via-lime-500/5 to-transparent',
  },
  {
    emoji: '🧾',
    name: 'Traces',
    path: '/traces',
    purpose: 'Every Hermes request + response, side-by-side. Indispensable for prompt-regression debugging.',
    golden: [
      'Open Traces (admin)',
      'Filter by source',
      'Click a row to inspect bodies',
      'Clear if the table grows too large',
    ],
    hermes:
      'PR-1 added tenant_id + per-source redaction so dataset content never lands here.',
    accent: 'from-violet-500/15 via-violet-500/5 to-transparent',
    admin: true,
  },
  {
    emoji: '🛠️',
    name: 'Auto-Fixes',
    path: '/autofix',
    purpose:
      'Every uncaught exception captured + (in dev mode) auto-fixed by Claude Agent SDK.',
    golden: [
      'Open Auto-Fixes (admin)',
      'Glance at the stats panel',
      'Filter by status',
      'Inspect a row → diff + correlation IDs',
    ],
    hermes:
      'Prod: dedup-by-fingerprint GitHub issues. Dev: Claude SDK proposes a fix on a sandbox branch, never touches main.',
    accent: 'from-rose-500/15 via-rose-500/5 to-transparent',
    admin: true,
  },
];

type ServiceCard = { name: string; tagline: string; detail: string; emoji: string };
const SERVICES: ServiceCard[] = [
  {
    emoji: '🧠',
    name: 'API',
    tagline: 'FastAPI + SQLite',
    detail:
      'The brain. Authenticates (Keycloak), enforces policy (OPA), persists every Run / Session / Dataset / Export / Trace / AutoFix.',
  },
  {
    emoji: '🖥️',
    name: 'UI',
    tagline: 'React 19 + Vite',
    detail:
      'The only thing users touch. Tailwind-styled, react-router 7. Talks to the API over HTTPS + SSE.',
  },
  {
    emoji: '🏋️',
    name: 'Trainer worker',
    tagline: 'MLX or CUDA',
    detail:
      'Host process. Claims queued runs, shells out to mlx_lm.lora (Apple) or PEFT+TRL (NVIDIA). Streams metrics back.',
  },
  {
    emoji: '🔁',
    name: 'Ratchet worker',
    tagline: 'Autoresearch loop',
    detail:
      'The autoresearch brain. Walks sessions through N rounds, calling Hermes between rounds for mutations.',
  },
  {
    emoji: '📦',
    name: 'Exporter worker',
    tagline: 'GGUF quantize',
    detail:
      'Fuses LoRA/DoRA adapters back into the base, then runs llama-quantize to produce GGUF artifacts.',
  },
  {
    emoji: '🦙',
    name: 'Ollama',
    tagline: 'qwen3:30b-a3b',
    detail:
      'Local LLM server. Every "Hermes" call lands here. Configurable via HERMES_MODEL and OLLAMA_URL.',
  },
  {
    emoji: '🧩',
    name: 'MCP server',
    tagline: 'Model Context Protocol',
    detail:
      'Exposes SLM-Forge tools to Claude Desktop / Cursor / Claude Code CLI as MCP tools.',
  },
  {
    emoji: '🔑',
    name: 'Keycloak',
    tagline: 'SSO + JWT',
    detail:
      'Off by default. make auth ENABLED=true flips on Keycloak + OPA enforcement.',
  },
  {
    emoji: '🛡️',
    name: 'OPA',
    tagline: 'Rego policy engine',
    detail:
      'Fine-grained authorization. Policies live in policies/. Same on/off switch as Keycloak.',
  },
  {
    emoji: '📈',
    name: 'Observability',
    tagline: 'Prometheus + Loki + Grafana',
    detail:
      'JSON logs flow workers → Promtail → Loki; Prometheus scrapes /metrics. make obs-up brings them up.',
  },
  {
    emoji: '🩹',
    name: 'error-responder',
    tagline: 'Self-healing layer (PR-A + PR-B)',
    detail:
      'Captures uncaught exceptions, fingerprints + redacts them, routes to GitHub issue (prod) or Claude SDK auto-fix (dev).',
  },
];

type Integration = { where: string; skill: string; user: string; backend: string };
const HERMES_MAP: Integration[] = [
  {
    where: 'Dashboard → /hermes/status',
    skill: '—',
    user: 'Status pill',
    backend: 'Probes Ollama version + model.',
  },
  {
    where: 'Experiments → /hermes/select-method',
    skill: 'select_method_for_task',
    user: '"Ask Hermes" → method + rationale',
    backend: 'One LLM call; returns LoRA/DoRA/full + hyperparams.',
  },
  {
    where: 'Experiments → ratchet (background)',
    skill: 'propose_hyperparam_mutation',
    user: 'Round-over-round hyperparameter shifts',
    backend: 'Ratchet calls Hermes between rounds; mutation logged.',
  },
  {
    where: 'Datasets → preview',
    skill: 'ingest_dataset',
    user: 'Auto-detected prompt / response fields',
    backend: 'Used when universal-format detection isn\'t enough.',
  },
  {
    where: 'Datasets → /synthesize',
    skill: 'synthesize_style_prompt',
    user: 'Background dataset expansion',
    backend: '100 seed rows → 1k via Hermes.',
  },
  {
    where: 'Datasets → preview (PR-4)',
    skill: 'data_quality_review',
    user: 'Warnings panel (dupes, PII, off-topic)',
    backend: 'Background scan of first 50 rows; polled at /ingest/qa/{qa_id}.',
  },
  {
    where: 'Runs → failure (PR-2)',
    skill: 'failure_post_mortem',
    user: 'Auto-generated Markdown diagnosis',
    backend: 'Triggered on status=failed; stored on run + sidecar file.',
  },
  {
    where: 'Exports → quant picker',
    skill: 'recommend_export_quants',
    user: '"Q4_K_M for iPhone"',
    backend: 'One LLM call; plain-English advice.',
  },
  {
    where: 'Runs / Synth 4xx (PR-3)',
    skill: 'error_remedy',
    user: 'detail.remedy field on the error response',
    backend: 'Inline call with 4s wall-clock cap; falls back to null.',
  },
  {
    where: 'Chat → SSE stream',
    skill: '(every chat-agent tool)',
    user: 'Streamed tool cards + final answer',
    backend: 'LangGraph state machine; Hermes is the only LLM call.',
  },
  {
    where: 'R&D → /research/reports',
    skill: 'report_writer',
    user: 'Markdown report',
    backend: 'Web search → Hermes → Markdown.',
  },
  {
    where: 'Agents → /agents/{id}/run',
    skill: 'evaluation_designer, incident_responder',
    user: 'Multi-step run with per-step trace',
    backend: 'Each step is a separate Hermes call.',
  },
  {
    where: 'Traces (admin)',
    skill: '(all of the above)',
    user: 'Request + response side-by-side',
    backend: 'PR-1 added tenant_id + per-source redaction.',
  },
  {
    where: 'Auto-Fixes (admin, PR-A + PR-B)',
    skill: '(Claude Agent SDK, not Hermes)',
    user: 'Captured errors + proposed fixes + status',
    backend: 'Dev: SDK auto-fix loop. Prod: dedup-by-fingerprint GitHub issue.',
  },
];

const SKILLS: { name: string; purpose: string }[] = [
  { name: 'analyze_canary_drift', purpose: 'Spot overfitting from canary vs val loss.' },
  { name: 'auto_label_unlabeled', purpose: 'Synthesize chat-style prompts for raw text.' },
  { name: 'data_quality_review', purpose: 'Find dupes / PII / off-topic / format mismatches.' },
  { name: 'diagnose_mps_oom', purpose: 'Recommend fixes for Apple Silicon OOMs.' },
  { name: 'error_remedy', purpose: 'Translate raw API errors into 1-3 sentences of help.' },
  { name: 'explain_metric_anomaly', purpose: 'Plain-English read of weird loss curves.' },
  { name: 'failure_post_mortem', purpose: '5-section Markdown diagnosis for failed runs.' },
  { name: 'ingest_dataset', purpose: 'Route source → endpoint, suggest field mapping.' },
  { name: 'model_selection', purpose: 'Pick a base model by task + device.' },
  { name: 'propose_canary_set', purpose: 'Generate 5 edge-case canary records.' },
  { name: 'propose_hyperparam_mutation', purpose: 'Next hyperparam suggestion for the ratchet.' },
  { name: 'recommend_export_quants', purpose: 'GGUF quant level by target device.' },
  { name: 'select_method_for_task', purpose: 'LoRA vs DoRA vs full by task + model size.' },
  { name: 'synthesize_style_prompt', purpose: 'Distil seed-set style into guidance.' },
];

// ─── Helpers ─────────────────────────────────────────────────────────────

function SectionHeading({
  id,
  eyebrow,
  title,
  subtitle,
}: {
  id: string;
  eyebrow: string;
  title: string;
  subtitle?: string;
}) {
  return (
    <header id={id} className="mb-6 scroll-mt-20">
      <div className="text-[11px] uppercase tracking-[0.18em] text-emerald-400/80">
        {eyebrow}
      </div>
      <h2 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-100 sm:text-3xl">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-400">{subtitle}</p>
      )}
    </header>
  );
}

function GradientCard({
  accent,
  children,
  className = '',
}: {
  accent: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`group relative overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40 p-5 transition hover:border-zinc-700 hover:bg-zinc-900/60 hover:shadow-lg hover:shadow-black/40 ${className}`}
    >
      <div
        aria-hidden
        className={`absolute inset-0 -z-0 bg-gradient-to-br ${accent} opacity-90 transition group-hover:opacity-100`}
      />
      <div className="relative">{children}</div>
    </div>
  );
}

function Pill({
  children,
  tone = 'zinc',
}: {
  children: React.ReactNode;
  tone?: 'zinc' | 'emerald' | 'rose';
}) {
  const tones: Record<string, string> = {
    zinc: 'border-zinc-700 bg-zinc-800/60 text-zinc-300',
    emerald: 'border-emerald-700 bg-emerald-500/10 text-emerald-300',
    rose: 'border-rose-700 bg-rose-500/10 text-rose-300',
  };
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────

const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'features', label: 'Tabs' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'services', label: 'Microservices' },
  { id: 'hermes', label: 'Hermes Map' },
  { id: 'skills', label: 'Skills' },
  { id: 'demo', label: 'Demo Checklist' },
];

export default function Product() {
  const [active, setActive] = useState<string>('overview');

  // Highlight the current section in the side nav as the user scrolls.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible) setActive(visible.target.id);
      },
      { rootMargin: '-30% 0px -55% 0px', threshold: [0.1, 0.5] },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  const skillsSorted = useMemo(() => SKILLS.slice().sort((a, b) => a.name.localeCompare(b.name)), []);

  return (
    <div className="mx-auto max-w-7xl text-zinc-100">
      {/* ─── Hero ───────────────────────────────────────────────── */}
      <section
        id="overview"
        className="relative scroll-mt-20 overflow-hidden rounded-2xl border border-zinc-800 bg-gradient-to-br from-emerald-500/15 via-indigo-500/10 to-zinc-900 p-8 sm:p-12"
      >
        <div
          aria-hidden
          className="absolute -right-24 -top-24 h-72 w-72 rounded-full bg-emerald-500/20 blur-3xl"
        />
        <div
          aria-hidden
          className="absolute -bottom-32 -left-12 h-72 w-72 rounded-full bg-indigo-500/20 blur-3xl"
        />
        <div className="relative max-w-3xl">
          <Pill tone="emerald">SLM-Forge</Pill>
          <h1 className="mt-3 text-3xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Fine-tune small LLMs in your lab.
            <br />
            <span className="text-emerald-300">
              Let Hermes do the research.
            </span>
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-zinc-300 sm:text-lg">
            A local-first lab for fine-tuning, evaluating, and exporting small
            language models — driven by a local Hermes / Ollama agent that
            picks methods, proposes hyperparameters, writes post-mortems, and
            (in dev mode) even fixes bugs in its own codebase. Your data never
            leaves the machine.
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            <a
              href="#features"
              className="rounded-md bg-emerald-500 px-4 py-2 text-sm font-medium text-emerald-950 hover:bg-emerald-400"
            >
              Tour the tabs ↓
            </a>
            <a
              href="#hermes"
              className="rounded-md border border-zinc-700 bg-zinc-900/60 px-4 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Where Hermes helps
            </a>
            <a
              href="/docs/SLM_FORGE_PRODUCT_GUIDE.md"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md border border-zinc-700 bg-zinc-900/60 px-4 py-2 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Open the written guide
            </a>
          </div>
          <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ['12', 'tabs'],
              ['14', 'Hermes skills'],
              ['~30 s', 'first run'],
              ['100 %', 'local'],
            ].map(([n, label]) => (
              <div
                key={label}
                className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"
              >
                <div className="text-2xl font-semibold text-emerald-300">{n}</div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  {label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Layout with sidebar + scrolling content ─────────── */}
      <div className="mt-10 grid grid-cols-1 gap-10 lg:grid-cols-[200px_1fr]">
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <nav className="space-y-1 text-sm">
            {SECTIONS.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className={`block rounded-md px-3 py-1.5 transition ${
                  active === s.id
                    ? 'bg-emerald-500/10 text-emerald-300'
                    : 'text-zinc-400 hover:text-zinc-100'
                }`}
              >
                {s.label}
              </a>
            ))}
          </nav>
        </aside>

        <div className="space-y-16">
          {/* ─── Tabs grid ─────────────────────────────────────── */}
          <section>
            <SectionHeading
              id="features"
              eyebrow="What's inside"
              title="The 12 tabs, each with a job to do."
              subtitle="Every tab here has a single, clear purpose. Click any card to jump straight in."
            />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {TABS.map((t) => (
                <GradientCard key={t.path} accent={t.accent}>
                  <div className="flex items-start gap-3">
                    <div className="text-3xl leading-none">{t.emoji}</div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <a
                          href={t.path}
                          className="text-lg font-semibold text-zinc-100 hover:text-emerald-300"
                        >
                          {t.name}
                        </a>
                        {t.admin && <Pill tone="rose">admin</Pill>}
                      </div>
                      <p className="mt-1 text-sm leading-snug text-zinc-300">{t.purpose}</p>
                      <ol className="mt-3 list-decimal space-y-0.5 pl-5 text-[12px] text-zinc-400">
                        {t.golden.map((step) => (
                          <li key={step}>{step}</li>
                        ))}
                      </ol>
                      <p className="mt-3 rounded-md border border-emerald-700/30 bg-emerald-500/5 p-2 text-[11px] leading-snug text-emerald-200">
                        <span className="font-semibold text-emerald-300">Hermes here:</span>{' '}
                        {t.hermes}
                      </p>
                    </div>
                  </div>
                </GradientCard>
              ))}
            </div>
          </section>

          {/* ─── Architecture ────────────────────────────────── */}
          <section>
            <SectionHeading
              id="architecture"
              eyebrow="Where the boxes live"
              title="Browser → Docker → Host. GPUs stay on the host."
              subtitle="Docker for the API + UI (repeatable). Host for the workers + Ollama (so they can see the GPU)."
            />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {[
                {
                  emoji: '🌐',
                  title: 'Browser',
                  body: 'React 19 + Vite + Tailwind. Connects via HTTPS + SSE.',
                  items: ['UI on :5173'],
                  border: 'border-emerald-700/40',
                },
                {
                  emoji: '🐳',
                  title: 'Docker',
                  body: 'API + auth + observability. Reproducible across machines.',
                  items: ['FastAPI on :8000', 'Keycloak :8080', 'OPA :8181', 'Prom + Loki + Grafana'],
                  border: 'border-indigo-700/40',
                },
                {
                  emoji: '🖧',
                  title: 'Host',
                  body: 'Anything that needs the GPU + Ollama. Long-lived processes.',
                  items: ['Trainer (MLX / CUDA)', 'Ratchet', 'Exporter', 'Ollama :11434'],
                  border: 'border-amber-700/40',
                },
              ].map((stack) => (
                <div
                  key={stack.title}
                  className={`rounded-xl border ${stack.border} bg-zinc-900/40 p-5`}
                >
                  <div className="text-3xl">{stack.emoji}</div>
                  <h3 className="mt-2 text-lg font-semibold">{stack.title}</h3>
                  <p className="mt-1 text-sm text-zinc-400">{stack.body}</p>
                  <ul className="mt-3 space-y-1 text-[12px] text-zinc-300">
                    {stack.items.map((it) => (
                      <li key={it} className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                        {it}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>

          {/* ─── Microservices ───────────────────────────────── */}
          <section>
            <SectionHeading
              id="services"
              eyebrow="The moving parts"
              title="Microservices catalog."
              subtitle="Each is single-purpose, observable, and replaceable. Workers and API don't share state — they talk via HTTP."
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {SERVICES.map((svc) => (
                <div
                  key={svc.name}
                  className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-zinc-700 hover:bg-zinc-900/60"
                >
                  <div className="flex items-center gap-2">
                    <div className="text-2xl">{svc.emoji}</div>
                    <div>
                      <div className="text-sm font-semibold text-zinc-100">{svc.name}</div>
                      <div className="text-[11px] uppercase tracking-wide text-emerald-300">
                        {svc.tagline}
                      </div>
                    </div>
                  </div>
                  <p className="mt-2 text-[12px] leading-snug text-zinc-400">{svc.detail}</p>
                </div>
              ))}
            </div>
          </section>

          {/* ─── Hermes / Ollama map ────────────────────────── */}
          <section>
            <SectionHeading
              id="hermes"
              eyebrow="The LLM moments"
              title="Hermes / Ollama integration map."
              subtitle="Every place a Hermes skill earns its keep, in one table. Sorted by tab order."
            />
            <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/40">
              <table className="w-full table-fixed text-left text-[12px]">
                <thead className="bg-zinc-900 text-[10px] uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th className="w-[22%] px-3 py-2">Where</th>
                    <th className="w-[20%] px-3 py-2">Skill</th>
                    <th className="w-[28%] px-3 py-2">What the user sees</th>
                    <th className="w-[30%] px-3 py-2">Behind the scenes</th>
                  </tr>
                </thead>
                <tbody>
                  {HERMES_MAP.map((row) => (
                    <tr
                      key={row.where}
                      className="border-t border-zinc-800 align-top text-zinc-300 hover:bg-zinc-900/60"
                    >
                      <td className="px-3 py-2 font-medium text-emerald-200">{row.where}</td>
                      <td className="px-3 py-2 font-mono text-[11px] text-amber-200">{row.skill}</td>
                      <td className="px-3 py-2">{row.user}</td>
                      <td className="px-3 py-2 text-zinc-400">{row.backend}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* ─── Skills catalog ─────────────────────────────── */}
          <section>
            <SectionHeading
              id="skills"
              eyebrow="The skill library"
              title="14 reusable Hermes skills."
              subtitle="Each skill is a Markdown system prompt under .hermes-skills/. Hermes loads the file and the runtime feeds it the user message + any JSON context."
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {skillsSorted.map((s) => (
                <div
                  key={s.name}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 transition hover:border-emerald-700/40 hover:bg-zinc-900/60"
                >
                  <div className="font-mono text-[12px] text-amber-200">{s.name}</div>
                  <p className="mt-1 text-[12px] text-zinc-400">{s.purpose}</p>
                </div>
              ))}
            </div>
          </section>

          {/* ─── Demo checklist ─────────────────────────────── */}
          <section>
            <SectionHeading
              id="demo"
              eyebrow="Show the client"
              title="A 10-minute demo checklist."
              subtitle="If every box ticks green on a clean checkout, the entire pipeline is healthy."
            />
            <ol className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {[
                'Dashboard — every worker tile green.',
                'Hermes status card — Ollama up, model pulled, skills loaded.',
                'Datasets → upload → confirm QA panel flips pending → ready (PR-4).',
                'Experiments → "Ask Hermes" → method suggestion with rationale.',
                'Experiments → start → ratchet round 1 logs a mutation_reasoning.',
                'Runs → induce a fail → post_mortem Markdown appears in ~60s (PR-2).',
                'Runs → POST an uncataloged model → 422 with detail.remedy (PR-3).',
                'Exports → queue → download GGUF → load in PocketPal or llama.cpp.',
                'Chat → "list my last 5 runs" → tool cards stream in.',
                'Traces (admin) → confirm skill:data_quality_review rows are redacted.',
                'Auto-Fixes (admin, dev) → induce a NameError → status=deployed on a sandbox branch; main unchanged.',
                'make obs-up → Grafana shows trainer JSON logs flowing.',
              ].map((step, idx) => (
                <li
                  key={step}
                  className="flex items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3"
                >
                  <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-[11px] font-semibold text-emerald-300">
                    {idx + 1}
                  </span>
                  <span className="text-[13px] text-zinc-300">{step}</span>
                </li>
              ))}
            </ol>
          </section>

          {/* ─── Footer ─────────────────────────────────────── */}
          <footer className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-5 text-[12px] text-zinc-500">
            Written guide: <code className="text-emerald-300">docs/SLM_FORGE_PRODUCT_GUIDE.md</code>.
            Ultraplan: <code className="text-emerald-300">docs/ultra_plan_Hermes_hardning.md</code>.
            This page mirrors the guide section-for-section; the Markdown is the source of truth for
            anything in conflict.
          </footer>
        </div>
      </div>
    </div>
  );
}
