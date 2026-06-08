# 2-minute demo video script

Use any screen recorder (QuickTime works fine). Keep it under 2:30. Voiceover is optional but adds 30% to impact.

## Setup before recording

- All four terminals running (`make dev`, `make trainer`, `make ratchet`, `make exporter`)
- Browser at http://localhost:5173/
- Database NOT empty — have at least one completed run already
- iPhone next to your screen with PocketPal AI open

## Timing breakdown

### 0:00–0:15 — Title + premise (15s)
> "This is SLM-Forge. It fine-tunes small language models on a MacBook and deploys them to iPhone. All offline."

Show: Dashboard at `/`. Read the tagline aloud.

### 0:15–0:30 — Data in (15s)
> "Ingest your data from anywhere — file, URL, web scrape, or S3."

Show: Click "+ Dataset" → flip through the four source tabs → upload a small JSONL → land on the preview screen.

### 0:30–1:00 — Train + autoresearch (30s)
> "Start an experiment. Hermes Agent — running on local Ollama — proposes hyperparameter mutations. The ratchet keeps improvements, discards regressions. No PyTorch, no CUDA — pure MLX."

Show: Navigate to a running experiment → ratchet timeline graph descending → zoom on the green/red dots → switch to a single run page showing live loss curves.

### 1:00–1:30 — Export to GGUF (30s)
> "Click 'Export to GGUF'. Behind the scenes: LoRA fuses into the base, converts to GGUF, quantizes to Q4_K_M and Q8_0."

Show: Run detail page → click "Export to GGUF →" → land on /exports showing progress → completed state.

### 1:30–2:00 — iPhone deployment (30s)
> "AirDrop the Q4_K_M file to your iPhone. Open PocketPal AI. Add local model. Done. Fully offline."

Show: AirDrop dialog → PocketPal "Add Local Model" → loaded model → chat reply appearing.

### 2:00–2:20 — Maintenance + close (20s)
> "Disk usage and cleanup are built in. Open source, MIT licensed."

Show: /maintenance page briefly → GitHub URL on screen at the end.

## Voice / tone

Direct, factual, no hype words ("revolutionary", "game-changing"). The product is technical enough that overselling makes it less credible.

## Music

Optional. If used, keep it under -20dB so voice stays primary.
