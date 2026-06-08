import { API_URL } from './api';

export type LengthStats = {
  min: number;
  p50: number;
  p90: number;
  max: number;
  mean: number;
};

export type DatasetDetail = {
  name: string;
  description: string;
  readme_markdown: string;
  train_count: number;
  valid_count: number;
  canary_count: number;
  has_canary: boolean;
  length_stats: LengthStats;
  train_preview: Record<string, unknown>[];
  valid_preview: Record<string, unknown>[];
  canary_preview: Record<string, unknown>[];
};

export type SplitName = 'train' | 'valid' | 'canary';

export type RowsResponse = {
  split: SplitName;
  offset: number;
  limit: number;
  total: number;
  rows: Record<string, unknown>[];
};

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API_URL}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}

export const datasetsApi = {
  getDetail: (name: string) =>
    jget<DatasetDetail>(`/api/v1/datasets/${encodeURIComponent(name)}`),
  getRows: (name: string, split: SplitName, offset: number, limit: number) =>
    jget<RowsResponse>(
      `/api/v1/datasets/${encodeURIComponent(name)}/rows?split=${split}&offset=${offset}&limit=${limit}`,
    ),
};
