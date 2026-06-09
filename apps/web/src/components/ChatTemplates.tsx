import { useState } from 'react';

/**
 * Categorized library of clickable prompt templates covering the full
 * SLM-Forge lifecycle. Click any template -> ``onPick(text)`` fires and the
 * parent inserts the prompt into the chat textarea + focuses it.
 *
 * Categories follow the user-facing workflow:
 *   1. Datasets   2. Experiments   3. Runs   4. Hermes/Autoresearch
 *   5. Exports    6. Diagnostics
 */

type Template = { title: string; prompt: string };
type Group = { name: string; emoji: string; templates: Template[] };

const GROUPS: Group[] = [
  {
    name: 'Datasets',
    emoji: '📁',
    templates: [
      {
        title: 'List all datasets',
        prompt: 'List every dataset I have, with row counts and whether they have a canary set.',
      },
      {
        title: 'Preview first 5 rows',
        prompt: 'Show me the first 5 training records from the stock-analyst dataset.',
      },
      {
        title: 'Synthesize 200 more',
        prompt:
          'Synthesize 200 more examples from the stock-analyst dataset in the same style and write them as a new dataset called stock-analyst-expanded.',
      },
      {
        title: 'Check quality',
        prompt:
          'Review the stock-analyst dataset for duplicates, length outliers, and any rows missing a proper assistant response.',
      },
      {
        title: 'Propose a canary set',
        prompt:
          'Propose 5 canary examples for the recipe-extractor dataset that cover edge cases and unusual inputs.',
      },
    ],
  },
  {
    name: 'Experiments',
    emoji: '🧪',
    templates: [
      {
        title: 'Start autoresearch',
        prompt:
          'Start an autoresearch experiment on the stock-analyst dataset, 6 rounds, plateau patience 3.',
      },
      {
        title: 'Best hyperparams so far',
        prompt: 'What was the best hyperparameter combination across all my recent experiments?',
      },
      {
        title: 'Compare two runs',
        prompt: 'Compare runs 12 and 15 — show me the hyperparam diff and the loss curves.',
      },
      {
        title: 'Suggest next step',
        prompt:
          'Look at session 3 and tell me whether I should keep iterating, change the dataset, or stop.',
      },
    ],
  },
  {
    name: 'Runs',
    emoji: '⚡',
    templates: [
      {
        title: 'List running runs',
        prompt: 'List runs that are currently running, with their dataset and step count.',
      },
      {
        title: 'Show metrics',
        prompt:
          'Show me the train_loss and val_loss curves for run 5 — call out any anomalies.',
      },
      {
        title: 'Why did it fail?',
        prompt:
          'Run 7 failed — look at the training log tail and tell me the likely cause and a fix.',
      },
      {
        title: 'Summarize last 10',
        prompt:
          'Summarize the last 10 completed runs: dataset, method, final val_loss, time-to-completion.',
      },
    ],
  },
  {
    name: 'Hermes / Autoresearch',
    emoji: '🔬',
    templates: [
      {
        title: 'Propose next mutation',
        prompt:
          'Look at the iteration history for session 3 and propose the next hyperparameter mutation. Show me your reasoning.',
      },
      {
        title: 'Canary drift trend',
        prompt:
          'What is the canary drift trend across session 3? Are we overfitting to the val set?',
      },
      {
        title: 'Stop or keep going?',
        prompt:
          'Should I stop session 3 or keep going? Reason about diminishing returns and the canary drift.',
      },
      {
        title: 'Method recommendation',
        prompt:
          'Given the stock-analyst dataset (20 train rows, financial-analyst style), recommend a fine-tune method and base model.',
      },
    ],
  },
  {
    name: 'Exports',
    emoji: '📦',
    templates: [
      {
        title: 'Export to GGUF',
        prompt: 'Export run 5 to GGUF with Q4_K_M, Q5_K_M, and Q8_0 quants.',
      },
      {
        title: 'List completed exports',
        prompt: 'List all completed exports with their size and which run they came from.',
      },
      {
        title: 'iPhone-ready quant',
        prompt:
          'Which quant level should I pick for iPhone deployment of run 5? Trade off quality vs file size.',
      },
      {
        title: 'Estimate file size',
        prompt: 'Estimate the iPhone-ready GGUF file size for run 5 at Q4_K_M.',
      },
    ],
  },
  {
    name: 'Multi-step agents',
    emoji: '🤖',
    templates: [
      {
        title: 'Experiment recommender',
        prompt:
          'Run the experiment_recommender agent on the stock-analyst dataset for a terse-tone Q&A model targeting iPhone Pro.',
      },
      {
        title: 'Optimization coach',
        prompt:
          'Run the optimization_coach on session 3 and tell me whether to continue, pivot, or stop.',
      },
      {
        title: 'Evaluation designer',
        prompt:
          'Run the evaluation_designer on the recipe-extractor dataset to produce canary set + benchmark questions.',
      },
      {
        title: 'Incident responder',
        prompt:
          'Run the incident_responder on the most recent failed run and post the markdown post-mortem.',
      },
    ],
  },
  {
    name: 'Diagnostics',
    emoji: '🩺',
    templates: [
      {
        title: 'Is Ollama up?',
        prompt: 'Is Ollama reachable and is the qwen3:30b-a3b model pulled?',
      },
      {
        title: 'Tail trainer log',
        prompt: 'Show me the last 50 lines from the trainer worker log.',
      },
      {
        title: 'Disk usage',
        prompt: 'What is the disk usage under /runs and /exports? Are there any orphan files?',
      },
      {
        title: 'Worker heartbeat',
        prompt:
          'Which workers are heartbeating? When was the last heartbeat for each? Anything stale?',
      },
    ],
  },
];

export default function ChatTemplates({ onPick }: { onPick: (text: string) => void }) {
  // All categories collapsed by default except the first — keeps the panel scannable.
  const [open, setOpen] = useState<Record<string, boolean>>({
    [GROUPS[0].name]: true,
  });

  const toggle = (name: string) =>
    setOpen((prev) => ({ ...prev, [name]: !prev[name] }));

  return (
    <div className="space-y-3 text-xs">
      <div>
        <h3 className="text-sm font-medium text-zinc-300">Templates</h3>
        <p className="mt-1 text-[11px] text-zinc-500">
          Click any prompt to drop it into the chat box. Edit before sending.
        </p>
      </div>

      <div className="space-y-1.5">
        {GROUPS.map((g) => {
          const isOpen = !!open[g.name];
          return (
            <div
              key={g.name}
              className="rounded-md border border-zinc-800 bg-zinc-900/40"
            >
              <button
                type="button"
                onClick={() => toggle(g.name)}
                aria-expanded={isOpen}
                className="flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-zinc-200 hover:bg-zinc-800/50"
              >
                <span className="flex items-center gap-2">
                  <span aria-hidden>{g.emoji}</span>
                  <span className="font-medium">{g.name}</span>
                  <span className="text-[10px] text-zinc-500">
                    {g.templates.length}
                  </span>
                </span>
                <span className="font-mono text-zinc-500">{isOpen ? '−' : '+'}</span>
              </button>
              {isOpen && (
                <ul className="border-t border-zinc-800 px-1.5 py-1.5 space-y-0.5">
                  {g.templates.map((t) => (
                    <li key={t.title}>
                      <button
                        type="button"
                        onClick={() => onPick(t.prompt)}
                        className="block w-full rounded px-2 py-1.5 text-left text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-100"
                      >
                        <div className="font-medium text-zinc-300">{t.title}</div>
                        <div className="mt-0.5 line-clamp-2 text-[11px] text-zinc-500">
                          {t.prompt}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
