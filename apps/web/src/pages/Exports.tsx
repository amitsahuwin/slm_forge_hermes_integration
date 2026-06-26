import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { type ExportRow, type ExportStatus, exportsApi, deletes } from '../lib/api';

const STATUS_STYLES: Record<ExportStatus, string> = {
  queued: 'text-hcl-dark/60',
  fusing: 'text-hcl-warning',
  converting: 'text-hcl-warning',
  quantizing: 'text-hcl-warning',
  completed: 'text-hcl-teal',
  failed: 'text-red-600',
  cancelled: 'text-hcl-dark/50',
};

function humanBytes(n: number | null): string {
  if (n === null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let val = n;
  let u = 0;
  while (val >= 1024 && u < units.length - 1) {
    val /= 1024;
    u++;
  }
  return `${val.toFixed(val > 10 ? 0 : 1)} ${units[u]}`;
}

export default function Exports() {
  const [items, setItems] = useState<ExportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      exportsApi.list()
        .then((rs) => alive && setItems(rs))
        .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    tick();
    const iv = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(iv); };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Exports</h1>
        <p className="mt-1 text-sm text-hcl-dark/50">
          Fine-tuned models exported to GGUF for use on iPhone (PocketPal AI / Edge Gallery).
        </p>
      </div>

      {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}

      {items === null ? (
        <div className="text-sm text-hcl-dark/50">Loading…</div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-hcl-light-blue px-6 py-10 text-center text-sm text-hcl-dark/50">
          No exports yet. Open a completed run from{' '}
          <Link to="/runs" className="text-hcl-teal hover:underline">Runs</Link>
          {' '}and click "Export to GGUF".
        </div>
      ) : (
        <ul className="space-y-3">
          {items.map((e) => (
            <li key={e.id} className="rounded-lg border border-hcl-light-blue bg-white p-4">
              <div className="flex items-baseline justify-between">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-sm text-hcl-dark">Export #{e.id}</span>
                  <span className="text-xs text-hcl-dark/50">
                    from <Link to={`/runs/${e.run_id}`} className="text-hcl-teal hover:underline">run #{e.run_id}</Link>
                  </span>
                  <span className="text-xs text-hcl-dark/50">·</span>
                  <span className="font-mono text-xs text-hcl-dark/50">{e.base_model.replace(/^mlx-community\//, '')}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`font-mono text-xs ${STATUS_STYLES[e.status]}`}>● {e.status}</span>
                  <button
                    onClick={async () => {
                      if (!confirm(`Delete export #${e.id}? This also removes the on-disk GGUF files.`)) return;
                      try {
                        await deletes.export(e.id);
                      } catch (err) {
                        alert(err instanceof Error ? err.message : String(err));
                      }
                    }}
                    className="text-xs text-hcl-dark/40 hover:text-red-500"
                    title="Delete export and GGUF files"
                  >
                    delete
                  </button>
                </div>
              </div>

              {e.progress_text && (
                <p className="mt-2 font-mono text-xs text-hcl-dark/60">{e.progress_text}</p>
              )}

              {e.error_message && (
                <p className="mt-2 font-mono text-xs text-red-600">{e.error_message}</p>
              )}

              {e.status === 'completed' && (
                <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
                  <Variant label="Q4_K_M (iPhone)" path={e.gguf_q4_path} bytes={e.gguf_q4_bytes} href={exportsApi.downloadUrl(e.id, 'q4')} highlight />
                  <Variant label="Q5_K_M" path={e.gguf_q5_path} bytes={e.gguf_q5_bytes} href={exportsApi.downloadUrl(e.id, 'q5')} />
                  <Variant label="Q8_0" path={e.gguf_q8_path} bytes={e.gguf_q8_bytes} href={exportsApi.downloadUrl(e.id, 'q8')} />
                  <Variant label="F16 (reference)" path={e.gguf_f16_path} bytes={e.gguf_f16_bytes} href={exportsApi.downloadUrl(e.id, 'f16')} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-lg border border-hcl-light-blue bg-white p-4 text-xs text-hcl-dark/60">
        <strong className="text-hcl-dark/80">iPhone deployment:</strong> download the <code className="font-mono">Q4_K_M.gguf</code> file,
        AirDrop it to your iPhone, then open PocketPal AI → "Add Local Model" → select the file.
        Full instructions in <code className="font-mono">docs/IPHONE_DEPLOY.md</code>.
      </div>
    </div>
  );
}

function Variant({
  label, path, bytes, href, highlight,
}: {
  label: string;
  path: string | null;
  bytes: number | null;
  href: string;
  highlight?: boolean;
}) {
  if (!path) {
    return (
      <div className="rounded-md border border-hcl-light-blue bg-white px-3 py-2">
        <div className="font-mono text-xs text-hcl-dark/40">{label}</div>
        <div className="mt-0.5 font-mono text-xs text-hcl-dark/30">not produced</div>
      </div>
    );
  }
  return (
    <a
      href={href}
      className={`rounded-md border px-3 py-2 transition-colors hover:bg-hcl-tech-grey/60 ${
        highlight ? 'border-hcl-teal/30 bg-hcl-teal/10' : 'border-hcl-light-blue bg-white'
      }`}
    >
      <div className="font-mono text-xs text-hcl-dark/60">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-hcl-dark">{humanBytes(bytes)} ↓</div>
    </a>
  );
}
