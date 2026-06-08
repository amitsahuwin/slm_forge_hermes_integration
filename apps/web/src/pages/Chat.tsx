import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { API_URL } from '../lib/api';

// ─── Types ────────────────────────────────────────────────────────

type Role = 'user' | 'assistant' | 'system' | 'tool';

type Conversation = {
  id: number;
  title: string;
  created_at: string;
};

type ToolResult = {
  tool: string;
  tool_call_id?: string;
  result: unknown;
};

type Message = {
  id: number | string;
  role: Role;
  content: string;
  tool_calls?: ToolResult[] | null;
  created_at?: string;
  // ephemeral fields (in-flight tool spinners)
  pending?: boolean;
  inflightTools?: { name: string; t0: number }[];
};

// ─── Page ─────────────────────────────────────────────────────────

export default function Chat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const esRef = useRef<EventSource | null>(null);

  // initial load
  useEffect(() => {
    void refreshConversations();
  }, []);

  // when active changes, fetch messages
  useEffect(() => {
    if (activeId == null) {
      setMessages([]);
      return;
    }
    void (async () => {
      try {
        const r = await fetch(
          `${API_URL}/api/v1/chat/conversations/${activeId}/messages`,
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const ms = (await r.json()) as Message[];
        setMessages(ms);
      } catch (e) {
        console.error('load messages failed', e);
      }
    })();
  }, [activeId]);

  // autoscroll
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // cleanup any SSE on unmount
  useEffect(() => () => esRef.current?.close(), []);

  const refreshConversations = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/chat/conversations`);
      if (!r.ok) return;
      const list = (await r.json()) as Conversation[];
      setConversations(list);
      if (activeId == null && list.length > 0) setActiveId(list[0].id);
    } catch (e) {
      console.error('list conversations failed', e);
    }
  }, [activeId]);

  const newConversation = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/v1/chat/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const c = (await r.json()) as Conversation;
      setConversations((cs) => [c, ...cs]);
      setActiveId(c.id);
      setMessages([]);
    } catch (e) {
      console.error('create conversation failed', e);
    }
  }, []);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;

    let cid = activeId;
    if (cid == null) {
      try {
        const r = await fetch(`${API_URL}/api/v1/chat/conversations`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const c = (await r.json()) as Conversation;
        setConversations((cs) => [c, ...cs]);
        cid = c.id;
        setActiveId(cid);
      } catch (e) {
        console.error(e);
        return;
      }
    }

    setInput('');
    // optimistic user message
    const optimistic: Message = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: text,
    };
    setMessages((m) => [...m, optimistic]);

    try {
      await fetch(
        `${API_URL}/api/v1/chat/conversations/${cid}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text }),
        },
      );
    } catch (e) {
      console.error('post message failed', e);
      return;
    }

    // open SSE stream
    setStreaming(true);
    const placeholder: Message = {
      id: `pending-${Date.now()}`,
      role: 'assistant',
      content: '',
      pending: true,
      inflightTools: [],
      tool_calls: [],
    };
    setMessages((m) => [...m, placeholder]);

    const es = new EventSource(
      `${API_URL}/api/v1/chat/conversations/${cid}/stream`,
    );
    esRef.current = es;

    const upd = (mut: (m: Message) => Message) => {
      setMessages((cur) => {
        const next = [...cur];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].id === placeholder.id) {
            next[i] = mut(next[i]);
            break;
          }
        }
        return next;
      });
    };

    es.addEventListener('tool_start', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        upd((m) => ({
          ...m,
          inflightTools: [
            ...(m.inflightTools ?? []),
            { name: d.name ?? 'tool', t0: Date.now() },
          ],
        }));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('tool_end', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data) as ToolResult & {
          name?: string;
        };
        upd((m) => ({
          ...m,
          tool_calls: [
            ...(m.tool_calls ?? []),
            { tool: d.name ?? 'tool', result: d.result, tool_call_id: d.tool_call_id },
          ],
          inflightTools: (m.inflightTools ?? []).filter(
            (t) => t.name !== (d.name ?? 'tool'),
          ),
        }));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('token', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        upd((m) => ({ ...m, content: d.text ?? m.content }));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('final', (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        upd((m) => ({
          ...m,
          content: d.text ?? m.content,
          pending: false,
          inflightTools: [],
        }));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('done', () => {
      es.close();
      esRef.current = null;
      setStreaming(false);
      upd((m) => ({ ...m, pending: false, inflightTools: [] }));
      void refreshConversations();
    });

    es.addEventListener('error', () => {
      es.close();
      esRef.current = null;
      setStreaming(false);
      upd((m) => ({
        ...m,
        pending: false,
        content: m.content || '(stream interrupted)',
      }));
    });
  }, [input, activeId, streaming, refreshConversations]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  };

  return (
    <div className="grid grid-cols-[16rem_1fr_20rem] gap-4 h-[calc(100vh-12rem)]">
      {/* Left: conversations */}
      <aside className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 flex flex-col">
        <button
          onClick={() => void newConversation()}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          + New chat
        </button>
        <div className="mt-3 flex-1 overflow-y-auto space-y-1">
          {conversations.map((c) => (
            <button
              key={c.id}
              onClick={() => setActiveId(c.id)}
              className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs ${
                c.id === activeId
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:bg-zinc-900'
              }`}
              title={c.title}
            >
              {c.title}
            </button>
          ))}
          {conversations.length === 0 && (
            <p className="text-xs text-zinc-600 px-2">No conversations yet.</p>
          )}
        </div>
      </aside>

      {/* Center: thread */}
      <section className="flex flex-col rounded-xl border border-zinc-800 bg-zinc-950">
        <div
          ref={threadRef}
          className="flex-1 overflow-y-auto p-4 space-y-4"
        >
          {messages.length === 0 ? (
            <EmptyState />
          ) : (
            messages.map((m) => <MessageRow key={m.id} message={m} />)
          )}
        </div>
        <div className="border-t border-zinc-800 p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            disabled={streaming}
            placeholder="Ask about your runs, datasets, or kick off an experiment…"
            className="w-full resize-none rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-600 focus:outline-none"
          />
          <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-600">
            <span>Enter to send · Shift+Enter for newline</span>
            <span>{streaming ? 'streaming…' : 'ready'}</span>
          </div>
        </div>
      </section>

      {/* Right: context panel */}
      <aside className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-500">
        <h3 className="text-sm font-medium text-zinc-300">Context</h3>
        <p className="mt-2">
          Try: <em>"list completed runs"</em>, <em>"metrics for run 5"</em>,
          <em>"start an experiment on my-data"</em>.
        </p>
      </aside>
    </div>
  );
}

// ─── Subviews ─────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="text-center text-zinc-500 mt-20">
      <p className="text-sm">No messages yet — say hi to the SLM-Forge copilot.</p>
    </div>
  );
}

function MessageRow({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-xl bg-emerald-600/90 px-3 py-2 text-sm text-white whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  // assistant
  return (
    <div className="flex">
      <div className="max-w-[90%] space-y-2">
        {message.content && (
          <div className="rounded-xl bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm text-zinc-100">
            <MarkdownLite text={message.content} />
          </div>
        )}
        {(message.inflightTools ?? []).map((t, i) => (
          <ToolChip key={`infl-${i}`} name={t.name} state="running" />
        ))}
        {(message.tool_calls ?? []).map((tr, i) => (
          <ToolCard key={`tr-${i}`} toolResult={tr} />
        ))}
        {message.pending && !message.content && (
          <div className="text-xs text-zinc-500 italic">thinking…</div>
        )}
      </div>
    </div>
  );
}

function MarkdownLite({ text }: { text: string }) {
  // Very minimal: split on double newlines for paragraphs, preserve single newlines.
  const blocks = text.split(/\n{2,}/);
  return (
    <>
      {blocks.map((b, i) => (
        <p key={i} className={i > 0 ? 'mt-2 whitespace-pre-wrap' : 'whitespace-pre-wrap'}>
          {b}
        </p>
      ))}
    </>
  );
}

function ToolChip({
  name,
  state,
  detail,
}: {
  name: string;
  state: 'running' | 'done';
  detail?: string;
}) {
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 text-[11px] text-zinc-400">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          state === 'running' ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'
        }`}
      />
      <span className="font-mono">{name}</span>
      {detail && <span className="text-zinc-500">· {detail}</span>}
    </div>
  );
}

// ─── Per-tool cards ───────────────────────────────────────────────

function ToolCard({ toolResult }: { toolResult: ToolResult }) {
  const { tool, result } = toolResult;

  if (tool === 'list_runs') return <RunsCard data={result} />;
  if (tool === 'get_run_metrics') return <MetricsCard data={result} />;
  if (tool === 'list_datasets') return <DatasetsCard data={result} />;
  if (tool === 'list_experiments') return <ExperimentsCard data={result} />;
  if (tool === 'start_experiment') return <StartExperimentCard data={result} />;
  if (tool === 'propose_hyperparams') return <ProposeHyperparamsCard data={result} />;
  if (tool === 'get_run_status') return <RunStatusCard data={result} />;
  if (tool === 'get_export_status') return <ExportStatusCard data={result} />;
  if (tool === 'search_docs') return <DocsCard data={result} />;

  // generic fallback
  return (
    <CardShell title={tool}>
      <pre className="text-[11px] font-mono text-zinc-400 whitespace-pre-wrap break-all">
        {JSON.stringify(result, null, 2)}
      </pre>
    </CardShell>
  );
}

function CardShell({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-500">
          {title}
        </span>
        {meta && <span className="text-[11px] text-zinc-500">{meta}</span>}
      </div>
      {children}
    </div>
  );
}

type RunRow = {
  id: number;
  dataset: string;
  base_model: string;
  status: string;
  iters: number;
  final_val_loss: number | null;
  iteration_number: number | null;
};

function RunsCard({ data }: { data: unknown }) {
  const rows = Array.isArray(data) ? (data as RunRow[]) : [];
  return (
    <CardShell title="list_runs" meta={`${rows.length} run(s)`}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-500 border-b border-zinc-800">
              <th className="text-left py-1 pr-2">ID</th>
              <th className="text-left py-1 pr-2">Dataset</th>
              <th className="text-left py-1 pr-2">Status</th>
              <th className="text-right py-1 pr-2">Iters</th>
              <th className="text-right py-1 pr-2">val_loss</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-zinc-900">
                <td className="py-1 pr-2 font-mono text-zinc-300">#{r.id}</td>
                <td className="py-1 pr-2 text-zinc-200">{r.dataset}</td>
                <td className="py-1 pr-2">
                  <StatusBadge status={r.status} />
                </td>
                <td className="py-1 pr-2 text-right text-zinc-300">{r.iters}</td>
                <td className="py-1 pr-2 text-right font-mono text-zinc-300">
                  {r.final_val_loss != null ? r.final_val_loss.toFixed(4) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CardShell>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'completed'
      ? 'bg-emerald-900/60 text-emerald-300'
      : status === 'failed'
        ? 'bg-rose-900/60 text-rose-300'
        : status === 'running'
          ? 'bg-amber-900/60 text-amber-300'
          : 'bg-zinc-800 text-zinc-400';
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-mono ${cls}`}>
      {status}
    </span>
  );
}

function MetricsCard({ data }: { data: unknown }) {
  const d = (data ?? {}) as {
    run_id?: number;
    series?: Record<string, { step: number; value: number }[]>;
    metric_count?: number;
  };
  const series = d.series ?? {};
  const names = Object.keys(series);
  return (
    <CardShell
      title="get_run_metrics"
      meta={`run #${d.run_id} · ${d.metric_count ?? 0} pts`}
    >
      {names.length === 0 ? (
        <p className="text-xs text-zinc-500">No metrics yet.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {names.map((name) => (
            <div key={name}>
              <div className="text-[11px] text-zinc-400 mb-1 font-mono">{name}</div>
              <div className="h-24">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={series[name]}>
                    <XAxis dataKey="step" hide />
                    <YAxis hide domain={['auto', 'auto']} />
                    <Tooltip
                      contentStyle={{
                        background: '#18181b',
                        border: '1px solid #27272a',
                        fontSize: 11,
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="value"
                      stroke="#10b981"
                      strokeWidth={1.5}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          ))}
        </div>
      )}
    </CardShell>
  );
}

type DatasetRow = {
  name: string;
  train_count: number;
  valid_count: number;
  has_canary: boolean;
  description?: string;
};

function DatasetsCard({ data }: { data: unknown }) {
  const rows = Array.isArray(data) ? (data as DatasetRow[]) : [];
  return (
    <CardShell title="list_datasets" meta={`${rows.length} dataset(s)`}>
      <ul className="space-y-1.5">
        {rows.map((d) => (
          <li
            key={d.name}
            className="flex items-baseline justify-between text-xs"
          >
            <span className="font-mono text-zinc-200">{d.name}</span>
            <span className="text-zinc-500">
              {d.train_count} train · {d.valid_count} valid
              {d.has_canary ? ' · canary' : ''}
            </span>
          </li>
        ))}
      </ul>
    </CardShell>
  );
}

type ExperimentRow = {
  id: number;
  name: string;
  dataset: string;
  status: string;
  current_round: number;
  max_rounds: number;
  best_metric_value: number | null;
};

function ExperimentsCard({ data }: { data: unknown }) {
  const rows = Array.isArray(data) ? (data as ExperimentRow[]) : [];
  return (
    <CardShell title="list_experiments" meta={`${rows.length} experiment(s)`}>
      <ul className="space-y-1">
        {rows.map((s) => (
          <li
            key={s.id}
            className="flex items-baseline justify-between text-xs gap-2"
          >
            <span className="font-mono text-zinc-300 shrink-0">#{s.id}</span>
            <span className="flex-1 truncate text-zinc-200">{s.name}</span>
            <StatusBadge status={s.status} />
            <span className="text-zinc-500">
              {s.current_round}/{s.max_rounds}
            </span>
          </li>
        ))}
      </ul>
    </CardShell>
  );
}

function StartExperimentCard({ data }: { data: unknown }) {
  const d = (data ?? {}) as {
    requires_confirmation?: boolean;
    payload?: Record<string, unknown>;
    summary?: string;
  };
  const payload = d.payload ?? {};
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState<null | { id?: number; error?: string }>(null);

  const confirm = async () => {
    setConfirming(true);
    try {
      const r = await fetch(`${API_URL}/api/v1/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const out = await r.json();
      setDone({ id: out.id });
    } catch (e) {
      setDone({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      setConfirming(false);
    }
  };

  return (
    <CardShell title="start_experiment" meta="confirmation required">
      <p className="text-sm text-zinc-200">{d.summary ?? 'Start this experiment?'}</p>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] font-mono">
        {Object.entries(payload).map(([k, v]) => (
          <div key={k} className="flex justify-between">
            <dt className="text-zinc-500">{k}</dt>
            <dd className="text-zinc-300 truncate ml-2">{String(v)}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-3 flex gap-2">
        <button
          disabled={confirming || done?.id != null}
          onClick={confirm}
          className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {done?.id ? 'Started' : confirming ? 'Starting…' : 'Start'}
        </button>
        <button
          disabled={confirming}
          className="rounded-md border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
        >
          Edit
        </button>
        <button
          disabled={confirming}
          onClick={() => setDone({ error: 'cancelled' })}
          className="rounded-md border border-zinc-700 px-3 py-1 text-xs text-zinc-400 hover:bg-zinc-800"
        >
          Cancel
        </button>
      </div>
      {done?.id != null && (
        <p className="mt-2 text-[11px] text-emerald-400">
          Started experiment #{done.id}.
        </p>
      )}
      {done?.error && (
        <p className="mt-2 text-[11px] text-rose-400">{done.error}</p>
      )}
    </CardShell>
  );
}

function ProposeHyperparamsCard({ data }: { data: unknown }) {
  const d = (data ?? {}) as {
    baseline?: Record<string, unknown>;
    proposal?: Record<string, unknown>;
    error?: string;
  };
  if (d.error) {
    return (
      <CardShell title="propose_hyperparams">
        <p className="text-xs text-rose-400">{d.error}</p>
      </CardShell>
    );
  }
  const baseline = d.baseline ?? {};
  const proposal = d.proposal ?? {};
  const reasoning = (proposal.reasoning as string | undefined) ?? '';
  const expected = (proposal.expected_outcome as string | undefined) ?? '';
  const numericKeys = ['learning_rate', 'batch_size', 'num_layers', 'iters', 'max_seq_length'];

  return (
    <CardShell title="propose_hyperparams">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-zinc-500">
            <th className="text-left py-1">key</th>
            <th className="text-right py-1">current</th>
            <th className="text-right py-1">proposed</th>
          </tr>
        </thead>
        <tbody>
          {numericKeys.map((k) => {
            const cur = baseline[k];
            const prop = proposal[k];
            const changed = prop != null && prop !== cur;
            return (
              <tr key={k} className="border-t border-zinc-800">
                <td className="py-1 text-zinc-400">{k}</td>
                <td className="py-1 text-right text-zinc-400">
                  {cur != null ? String(cur) : '—'}
                </td>
                <td
                  className={`py-1 text-right ${
                    changed ? 'text-emerald-400' : 'text-zinc-500'
                  }`}
                >
                  {prop != null ? String(prop) : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {reasoning && (
        <p className="mt-2 text-[11px] text-zinc-400 italic">{reasoning}</p>
      )}
      {expected && (
        <p className="mt-1 text-[11px] text-zinc-500">→ {expected}</p>
      )}
    </CardShell>
  );
}

function RunStatusCard({ data }: { data: unknown }) {
  const d = (data ?? {}) as {
    id?: number;
    status?: string;
    dataset?: string;
    base_model?: string;
    final_val_loss?: number | null;
  };
  return (
    <CardShell title="get_run_status" meta={`run #${d.id}`}>
      <div className="flex items-center gap-2 text-xs">
        <StatusBadge status={d.status ?? 'unknown'} />
        <span className="text-zinc-400">{d.dataset}</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-500 font-mono truncate">{d.base_model}</span>
      </div>
      {d.final_val_loss != null && (
        <p className="mt-2 text-[11px] text-zinc-400 font-mono">
          val_loss = {d.final_val_loss.toFixed(4)}
        </p>
      )}
    </CardShell>
  );
}

function ExportStatusCard({ data }: { data: unknown }) {
  const d = (data ?? {}) as {
    id?: number;
    run_id?: number;
    status?: string;
    progress_text?: string;
    quant_levels?: string;
  };
  return (
    <CardShell title="get_export_status" meta={`run #${d.run_id}`}>
      <div className="flex items-center gap-2 text-xs">
        <StatusBadge status={d.status ?? 'none'} />
        {d.quant_levels && (
          <span className="font-mono text-zinc-500">{d.quant_levels}</span>
        )}
      </div>
      {d.progress_text && (
        <p className="mt-1 text-[11px] text-zinc-400">{d.progress_text}</p>
      )}
    </CardShell>
  );
}

function DocsCard({ data }: { data: unknown }) {
  const rows = Array.isArray(data)
    ? (data as { file: string; line: number; text: string }[])
    : [];
  return (
    <CardShell title="search_docs" meta={`${rows.length} hit(s)`}>
      <ul className="space-y-2">
        {rows.map((r, i) => (
          <li key={i} className="text-[11px]">
            <span className="font-mono text-emerald-400">
              {r.file.split('/').slice(-2).join('/')}:{r.line}
            </span>
            <p className="text-zinc-400 mt-0.5">{r.text}</p>
          </li>
        ))}
      </ul>
    </CardShell>
  );
}

