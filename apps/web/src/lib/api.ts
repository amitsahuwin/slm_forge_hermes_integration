import { auth } from '../auth/keycloak';
import { toast } from './toast';

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/**
 * Phase M.5 — auth-aware fetch wrapper.
 *
 * - Injects `Authorization: Bearer <token>` when an access token is available.
 * - 401 → trigger login redirect (token is expired or missing).
 * - 403 → surface the API's `detail` as a toast.
 *
 * All app fetches should go through this. The legacy helpers (`jget` etc.)
 * already do; ad-hoc `fetch()` calls elsewhere have been migrated to
 * `authFetch` in the same patch.
 */
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  const token = auth.getAccessToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const res = await fetch(input, { ...init, headers });

  if (res.status === 401) {
    // Token expired or missing. Only redirect when auth is actually enabled;
    // in disabled mode a 401 means a real backend bug and shouldn't loop.
    if (!auth.disabled) {
      // Fire-and-forget; the redirect will navigate away anyway.
      void auth.login(window.location.pathname + window.location.search);
    }
    return res;
  }
  if (res.status === 403) {
    let detail = 'Forbidden';
    try {
      const j = await res.clone().json();
      if (j && typeof j.detail === 'string') detail = j.detail;
    } catch {
      /* ignore */
    }
    toast.error(detail);
  }
  return res;
}

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
  session_id: number | null;
  parent_run_id: number | null;
  iteration_number: number | null;
  was_accepted: boolean | null;
  mutation_reasoning: string | null;
  canary_loss: number | null;
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

export type SessionStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export type TrainingSession = {
  id: number;
  name: string;
  dataset: string;
  base_model: string;
  method: RunMethod;
  iters: number;
  batch_size: number;
  learning_rate: number;
  num_layers: number;
  max_seq_length: number;
  max_rounds: number;
  plateau_patience: number;
  min_delta: number;
  target_metric: 'val_loss' | 'canary_loss';
  canary_drift_threshold: number;
  status: SessionStatus;
  current_round: number;
  best_run_id: number | null;
  best_metric_value: number | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
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
  const r = await authFetch(`${API_URL}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await authFetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  // Runs
  listRuns: () => jget<Run[]>('/api/v1/runs'),
  getRun: (id: number) => jget<Run>(`/api/v1/runs/${id}`),
  createRun: (body: Partial<Run>) => jpost<Run>('/api/v1/runs', body),
  listMetrics: (id: number) => jget<Metric[]>(`/api/v1/runs/${id}/metrics`),
  // Sessions
  listSessions: () => jget<TrainingSession[]>('/api/v1/sessions'),
  getSession: (id: number) => jget<TrainingSession>(`/api/v1/sessions/${id}`),
  createSession: (body: Partial<TrainingSession>) =>
    jpost<TrainingSession>('/api/v1/sessions', body),
  listIterations: (id: number) => jget<Run[]>(`/api/v1/sessions/${id}/iterations`),
  // Datasets & models
  listDatasets: () => jget<DatasetInfo[]>('/api/v1/datasets'),
  listModels: () => jget<BaseModelInfo[]>('/api/v1/models'),
};

// ─── Phase 3 ingestion ────────────────────────────────────────

export type IngestPreview = {
  staging_id: string;
  source_type: 'upload' | 'url' | 'scrape' | 's3';
  format: string;
  detected_fields: string[];
  sample_rows: Record<string, unknown>[];
  total_rows: number;
};

export type FinalizeResponse = {
  dataset_name: string;
  total_input_rows: number;
  train_count: number;
  valid_count: number;
  canary_count: number;
  skipped: number;
};

export const ingest = {
  async previewUpload(file: File): Promise<IngestPreview> {
    const fd = new FormData();
    fd.append('file', file);
    const r = await authFetch(`${API_URL}/api/v1/ingest/upload/preview`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`Upload failed: HTTP ${r.status} — ${await r.text()}`);
    return r.json();
  },
  previewUrl: (u: string) =>
    authFetch(`${API_URL}/api/v1/ingest/url/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: u }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`URL fetch failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  previewScrape: (u: string) =>
    authFetch(`${API_URL}/api/v1/ingest/scrape/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: u }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Scrape failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  previewS3: (args: { s3_path: string; access_key?: string; secret_key?: string; region?: string }) =>
    authFetch(`${API_URL}/api/v1/ingest/s3/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`S3 fetch failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<IngestPreview>;
    }),
  finalize: (args: {
    staging_id: string;
    dataset_name: string;
    prompt_field: string;
    response_field: string;
    template: 'gemma' | 'llama3' | 'qwen' | 'raw';
    system_prompt?: string;
    valid_fraction?: number;
    canary_fraction?: number;
    overwrite?: boolean;
  }) =>
    authFetch(`${API_URL}/api/v1/ingest/finalize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Finalize failed: HTTP ${r.status} — ${await r.text()}`);
      return r.json() as Promise<FinalizeResponse>;
    }),
};

// ─── Phase 4 exports ──────────────────────────────────────────

export type ExportStatus =
  | 'queued' | 'fusing' | 'converting' | 'quantizing'
  | 'completed' | 'failed' | 'cancelled';

export type ExportRow = {
  id: number;
  run_id: number;
  base_model: string;
  method: string;
  quant_levels: string;
  status: ExportStatus;
  error_message: string | null;
  progress_text: string | null;
  fused_path: string | null;
  gguf_f16_path: string | null;
  gguf_q4_path: string | null;
  gguf_q5_path: string | null;
  gguf_q8_path: string | null;
  gguf_f16_bytes: number | null;
  gguf_q4_bytes: number | null;
  gguf_q5_bytes: number | null;
  gguf_q8_bytes: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export const exportsApi = {
  list: () => jget<ExportRow[]>('/api/v1/exports'),
  get: (id: number) => jget<ExportRow>(`/api/v1/exports/${id}`),
  create: (body: { run_id: number; quant_levels?: string[] }) =>
    jpost<ExportRow>('/api/v1/exports', body),
  downloadUrl: (id: number, variant: 'f16' | 'q4' | 'q5' | 'q8') =>
    `${API_URL}/api/v1/exports/${id}/download/${variant}`,
};

// ─── Phase 5B admin / maintenance ────────────────────────────

export type DiskUsageEntry = {
  label: string;
  path: string;
  bytes: number;
  items: number;
};

export type DiskUsageResponse = {
  entries: DiskUsageEntry[];
  total_bytes: number;
};

export type CleanupPlan = {
  rejected_runs: number[];
  bytes_freed_estimate: number;
  description: string;
};

export type CleanupResponse = {
  deleted_run_ids: number[];
  bytes_freed: number;
};

async function jdelete(path: string): Promise<void> {
  const r = await authFetch(`${API_URL}${path}`, { method: 'DELETE' });
  if (!r.ok && r.status !== 204) {
    let detail = '';
    try { detail = (await r.json()).detail ?? ''; } catch { /* ignore */ }
    throw new Error(`DELETE ${path} → HTTP ${r.status}${detail ? ` — ${detail}` : ''}`);
  }
}

export const admin = {
  diskUsage: () => jget<DiskUsageResponse>('/api/v1/admin/disk-usage'),
  cleanupPlan: () => jget<CleanupPlan>('/api/v1/admin/cleanup/plan'),
  cleanupExecute: () => jpost<CleanupResponse>('/api/v1/admin/cleanup/execute', {}),
};

// Add deletes to existing API objects
export const deletes = {
  run: (id: number) => jdelete(`/api/v1/runs/${id}`),
  session: (id: number) => jdelete(`/api/v1/sessions/${id}`),
  export: (id: number) => jdelete(`/api/v1/exports/${id}`),
};
