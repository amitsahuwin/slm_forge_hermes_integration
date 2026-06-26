import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { type IngestPreview, ingest } from '../lib/api';

type SourceType = 'upload' | 'url' | 'scrape' | 's3';
type Template = 'gemma' | 'llama3' | 'qwen' | 'raw';

export default function NewDataset() {
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2>(1);
  const [source, setSource] = useState<SourceType>('upload');
  const [preview, setPreview] = useState<IngestPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 inputs
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [s3Path, setS3Path] = useState('');
  const [s3Key, setS3Key] = useState('');
  const [s3Secret, setS3Secret] = useState('');
  const [s3Region, setS3Region] = useState('us-east-1');

  // Step 2 inputs
  const [datasetName, setDatasetName] = useState('');
  const [promptField, setPromptField] = useState('');
  const [responseField, setResponseField] = useState('');
  const [template, setTemplate] = useState<Template>('qwen');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [validFraction, setValidFraction] = useState(0.15);
  const [canaryFraction, setCanaryFraction] = useState(0.05);

  async function doPreview() {
    setError(null);
    setBusy(true);
    try {
      let p: IngestPreview;
      if (source === 'upload') {
        if (!file) throw new Error('Select a file');
        p = await ingest.previewUpload(file);
      } else if (source === 'url') {
        if (!url) throw new Error('URL required');
        p = await ingest.previewUrl(url);
      } else if (source === 'scrape') {
        if (!url) throw new Error('URL required');
        p = await ingest.previewScrape(url);
      } else {
        if (!s3Path) throw new Error('S3 path required');
        p = await ingest.previewS3({
          s3_path: s3Path,
          access_key: s3Key || undefined,
          secret_key: s3Secret || undefined,
          region: s3Region || undefined,
        });
      }
      setPreview(p);
      // best-effort field guesses
      const fields = p.detected_fields;
      const promptGuess = fields.find((f) =>
        /^(question|prompt|instruction|input|user|query|content)$/i.test(f),
      );
      const respGuess = fields.find((f) =>
        /^(answer|response|output|completion|assistant)$/i.test(f),
      );
      setPromptField(promptGuess ?? fields[0] ?? '');
      setResponseField(respGuess ?? fields[1] ?? fields[0] ?? '');
      setStep(2);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doFinalize() {
    if (!preview) return;
    setError(null);
    setBusy(true);
    try {
      await ingest.finalize({
        staging_id: preview.staging_id,
        dataset_name: datasetName,
        prompt_field: promptField,
        response_field: responseField,
        template,
        system_prompt: systemPrompt || undefined,
        valid_fraction: validFraction,
        canary_fraction: canaryFraction,
        overwrite: true,
      });
      navigate('/datasets');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New Dataset</h1>
        <p className="mt-1 text-sm text-hcl-dark/50">
          {step === 1
            ? 'Step 1 of 2 — pick a source and preview the rows.'
            : 'Step 2 of 2 — map fields onto the chat template, then save.'}
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 px-3 py-2 font-mono text-xs text-red-600">
          {error}
        </div>
      )}

      {step === 1 && (
        <section className="space-y-5">
          <div className="grid grid-cols-4 gap-2">
            {(['upload', 'url', 'scrape', 's3'] as SourceType[]).map((t) => (
              <button
                key={t}
                onClick={() => setSource(t)}
                className={`rounded-md border px-3 py-2 text-sm font-medium ${
                  source === t
                    ? 'border-hcl-teal bg-hcl-teal/10 text-hcl-teal'
                    : 'border-hcl-light-blue bg-white text-hcl-dark/60 hover:border-hcl-teal/30'
                }`}
              >
                {t === 'upload' && '📁 Upload file'}
                {t === 'url' && '🔗 URL'}
                {t === 'scrape' && '🌐 Web scrape'}
                {t === 's3' && '☁ S3 bucket'}
              </button>
            ))}
          </div>

          {source === 'upload' && (
            <Field label="File (.jsonl / .ndjson / .csv / .json)">
              <input
                type="file"
                accept=".jsonl,.ndjson,.csv,.json"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-hcl-tech-grey file:px-3 file:py-1 file:text-xs file:text-hcl-dark/80"
              />
              {file && (
                <p className="mt-1 font-mono text-xs text-hcl-dark/50">
                  {file.name} · {(file.size / 1024).toFixed(1)} KB
                </p>
              )}
            </Field>
          )}

          {source === 'url' && (
            <Field label="Direct file URL (must be jsonl/csv/json)">
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/dataset.jsonl"
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              />
            </Field>
          )}

          {source === 'scrape' && (
            <Field label="Web page URL (static HTML extraction via trafilatura)">
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/article"
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              />
              <p className="mt-1 text-xs text-hcl-dark/50">
                JS-heavy SPAs won't work. For those, save the rendered page and upload it as HTML.
              </p>
            </Field>
          )}

          {source === 's3' && (
            <>
              <Field label="S3 path">
                <input
                  type="text"
                  value={s3Path}
                  onChange={(e) => setS3Path(e.target.value)}
                  placeholder="s3://my-bucket/path/to/file.jsonl"
                  className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
                />
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="AWS access key (optional — uses env if blank)">
                  <input
                    type="text"
                    value={s3Key}
                    onChange={(e) => setS3Key(e.target.value)}
                    className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
                  />
                </Field>
                <Field label="AWS secret key">
                  <input
                    type="password"
                    value={s3Secret}
                    onChange={(e) => setS3Secret(e.target.value)}
                    className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
                  />
                </Field>
                <Field label="Region">
                  <input
                    type="text"
                    value={s3Region}
                    onChange={(e) => setS3Region(e.target.value)}
                    className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
                  />
                </Field>
              </div>
              <p className="font-mono text-xs text-hcl-dark/50">
                Credentials are sent to the local API and used in-memory only. Not stored.
              </p>
            </>
          )}

          <button
            onClick={doPreview}
            disabled={busy}
            className="rounded-md bg-hcl-dark-teal px-4 py-2 text-sm font-medium text-white hover:bg-hcl-teal disabled:cursor-not-allowed disabled:bg-hcl-light-blue"
          >
            {busy ? 'Fetching…' : 'Preview rows →'}
          </button>
        </section>
      )}

      {step === 2 && preview && (
        <section className="space-y-5">
          {/* Preview summary */}
          <div className="rounded-lg border border-hcl-light-blue bg-white p-4">
            <div className="flex items-baseline justify-between">
              <h3 className="text-xs font-medium uppercase tracking-wider text-hcl-dark/50">
                Preview
              </h3>
              <div className="font-mono text-xs text-hcl-dark/50">
                {preview.total_rows} row{preview.total_rows === 1 ? '' : 's'} · {preview.format} · {preview.source_type}
              </div>
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full font-mono text-xs">
                <thead className="bg-hcl-dark-blue text-white">
                  <tr>
                    {preview.detected_fields.map((f) => (
                      <th key={f} className="px-2 py-1.5 text-left font-medium">
                        {f}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="text-hcl-dark/80">
                  {preview.sample_rows.map((row, i) => (
                    <tr key={i} className="border-t border-hcl-light-blue">
                      {preview.detected_fields.map((f) => (
                        <td key={f} className="max-w-xs truncate px-2 py-1.5">
                          {String(row[f] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Schema mapping */}
          <Field label="Dataset name (lowercase, hyphens/underscores; will be the folder name)">
            <input
              type="text"
              value={datasetName}
              onChange={(e) => setDatasetName(e.target.value)}
              placeholder="my-domain-qa"
              pattern="^[a-z0-9][a-z0-9-_]*$"
              className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Prompt field">
              <select
                value={promptField}
                onChange={(e) => setPromptField(e.target.value)}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              >
                {preview.detected_fields.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Response field">
              <select
                value={responseField}
                onChange={(e) => setResponseField(e.target.value)}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              >
                {preview.detected_fields.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Chat template (match this to your target base model family)">
            <select
              value={template}
              onChange={(e) => setTemplate(e.target.value as Template)}
              className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
            >
              <option value="qwen">Qwen (default)</option>
              <option value="llama3">Llama 3</option>
              <option value="gemma">Gemma</option>
              <option value="raw">Raw (no template)</option>
            </select>
          </Field>

          <Field label="System prompt (optional — prepended to every example)">
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={2}
              placeholder="e.g. You are a helpful stock analyst."
              className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Validation fraction">
              <input
                type="number"
                value={validFraction}
                onChange={(e) => setValidFraction(parseFloat(e.target.value))}
                min={0}
                max={0.5}
                step={0.01}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              />
            </Field>
            <Field label="Canary fraction (held-out for Goodhart check)">
              <input
                type="number"
                value={canaryFraction}
                onChange={(e) => setCanaryFraction(parseFloat(e.target.value))}
                min={0}
                max={0.3}
                step={0.01}
                className="w-full rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-3 py-2 font-mono text-sm"
              />
            </Field>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setStep(1)}
              className="rounded-md border border-hcl-light-blue bg-hcl-tech-grey px-4 py-2 text-sm text-hcl-dark/80 hover:bg-hcl-tech-grey"
            >
              ← Back
            </button>
            <button
              onClick={doFinalize}
              disabled={busy || !datasetName}
              className="rounded-md bg-hcl-dark-teal px-4 py-2 text-sm font-medium text-white hover:bg-hcl-teal disabled:cursor-not-allowed disabled:bg-hcl-light-blue"
            >
              {busy ? 'Saving…' : `Save dataset (${preview.total_rows} rows)`}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

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
