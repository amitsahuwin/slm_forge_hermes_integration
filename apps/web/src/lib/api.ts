export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type RunMethod = 'lora' | 'dora' | 'full';

export type Run = {
  id: number;
  dataset: string;
  base_model: string;
  method: RunMethod;
  iters: number;
  batch_size: number;
  learning_rate: number;
  num_layers: number;
  max_seq_length: number;
  grad_checkpoint: boolean;
  seed: number;
  status: RunStatus;
  error_message: string | null;
  adapter_path: string | null;
  final_train_loss: number | null;
  final_val_loss: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type Metric = {
  id?: number;
  run_id?: number;
  step: number;
  name: string;
  value: number;
  recorded_at?: string;
};

export type DatasetInfo = {
  name: string;
  train_count: number;
  valid_count: number;
  has_canary: boolean;
  description: string;
};

export type BaseModelInfo = {
  hf_id: string;
  label: string;
  family: string;
  size_params: string;
  recommended_method: string;
  notes: string;
};

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API_URL}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  listRuns: () => jget<Run[]>('/api/v1/runs'),
  getRun: (id: number) => jget<Run>(`/api/v1/runs/${id}`),
  createRun: (body: Partial<Run>) => jpost<Run>('/api/v1/runs', body),
  listMetrics: (id: number) => jget<Metric[]>(`/api/v1/runs/${id}/metrics`),
  listDatasets: () => jget<DatasetInfo[]>('/api/v1/datasets'),
  listModels: () => jget<BaseModelInfo[]>('/api/v1/models'),
};
