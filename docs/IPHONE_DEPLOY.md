# iPhone deployment

Run your fine-tuned model on iPhone offline via PocketPal AI or Google Edge Gallery.

## Prerequisites

- PocketPal AI installed on iPhone (free on the App Store)
- An exported `.gguf` file from SLM-Forge (e.g. `model-Q4_K_M.gguf`)

## Recommended quantization

| Variant | Size (3B model) | Quality | Use when |
|---|---|---|---|
| **Q4_K_M** | ~1.9 GB | Good | **Default for iPhone** |
| Q5_K_M | ~2.3 GB | Better | Newer iPhones with plenty of storage |
| Q8_0 | ~3.2 GB | Near-F16 | Reference/comparison |
| F16 | ~6 GB | Full | Desktop / debugging |

## Transfer methods

### Option A — AirDrop (easiest)

1. In SLM-Forge UI, navigate to **Exports**
2. Click the `Q4_K_M (iPhone)` tile to download the file to your Mac
3. Right-click the downloaded `.gguf` → Share → AirDrop → your iPhone
4. On iPhone, accept and save to Files

### Option B — Upload to a private HuggingFace repo

If file size or AirDrop is annoying:

```bash
# On your Mac (one-time setup)
huggingface-cli login
huggingface-cli repo create my-finetuned-models --private --repo-type model

# Upload
huggingface-cli upload my-finetuned-models ./exports/<id>/gguf/model-Q4_K_M.gguf
```

Then in PocketPal AI: search `<your-username>/my-finetuned-models` → download.

### Option C — USB / Files app

1. Open Finder, connect iPhone
2. Drag the `.gguf` into the iPhone's Files area
3. In PocketPal, browse to that location

## Loading the model in PocketPal AI

1. Open PocketPal AI
2. Tap **"Add Local Model"** (or the `+` icon)
3. Browse Files → locate your `.gguf`
4. Tap to load (takes 5–15s for a 3B Q4_K_M)
5. Start chatting

## Tuning PocketPal's inference settings

For Qwen-based models, set:
- **Context length:** 4096 (or 8192 if your iPhone has 8GB+ RAM)
- **Chat template:** `Qwen2` (PocketPal auto-detects most of the time)
- **Temperature:** 0.4–0.7 for analysis tasks, 0.7–1.0 for creative

If responses look garbled, the chat template is probably wrong — manually
set it in PocketPal's per-model settings.

## Expected iPhone performance

On iPhone 15 Pro / 16 Pro (8GB RAM):
- Qwen2.5-3B Q4_K_M: ~12–18 tokens/sec
- Llama 3.2 3B Q4_K_M: ~10–15 tokens/sec

On iPhone 13 / 14 (4-6GB RAM):
- Same models: ~5–10 tokens/sec, may swap heavily

If the device gets warm or slow, try a smaller base model (1B-class) or
more aggressive quant (Q3_K_M — not produced by default, ask for it manually).
