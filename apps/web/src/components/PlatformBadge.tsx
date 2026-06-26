import { useEffect, useState } from 'react';
import { type PlatformInfo, api } from '../lib/api';

/**
 * Phase T — Platform info badge for the nav bar.
 * Shows OS, arch, GPU status, and default backend.
 */
export default function PlatformBadge() {
  const [platform, setPlatform] = useState<PlatformInfo | null>(null);

  useEffect(() => {
    api
      .getPlatformInfo()
      .then(setPlatform)
      .catch((e) => console.error('Failed to fetch platform info:', e));
  }, []);

  if (!platform) return null;

  const icon = platform.has_nvidia_gpu ? '🖥️' : platform.os === 'darwin' ? '🍎' : '🐧';
  const backendLabel = platform.default_backend === 'mlx' ? 'MLX' : 'CUDA';

  return (
    <div
      className="flex items-center gap-1.5 rounded-md bg-white/10 px-2 py-1 text-[11px] text-white/70"
      title={`${platform.platform_label} · Default: ${backendLabel}`}
    >
      <span>{icon}</span>
      <span className="font-medium text-white">{backendLabel}</span>
    </div>
  );
}
