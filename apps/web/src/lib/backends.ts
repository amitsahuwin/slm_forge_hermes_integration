// Phase U — shared training-backend metadata for the New Run + New Experiment
// pages, so backend selection behaves identically in both.
import type { CatalogModelV2, TrainerBackendName } from './api';

export const BACKEND_OPTIONS: { value: TrainerBackendName; label: string; tip: string }[] = [
  {
    value: 'mlx',
    label: 'Apple Silicon (this Mac)',
    tip: 'Runs via mlx-lm. Make sure `make trainer` is running in another terminal.',
  },
  {
    value: 'cuda',
    label: 'NVIDIA GPU worker',
    tip: 'Runs via PEFT + TRL. The job stays queued until a CUDA worker (`make trainer-cuda` or the Docker image) claims it.',
  },
];

/** First model whose variant for this backend exists and isn't broken. */
export function defaultModelId(models: CatalogModelV2[], backend: TrainerBackendName): string {
  for (const m of models) {
    const v = m.backends[backend];
    if (v && v.status !== 'broken') return v.model_id;
  }
  return '';
}
