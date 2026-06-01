# Screenshots to capture

A walkthrough of the UI for documentation and demos. Capture these in order and save to `docs/screenshots/`.

## 1. Dashboard (`http://localhost:5173/`)
   - Filename: `01-dashboard.png`
   - Shows capability matrix (all green after Phase 4)

## 2. New Session form (`/sessions/new`)
   - Filename: `02-new-session.png`
   - Hyperparameters configured, dataset = stock-analyst

## 3. Session detail with ratchet graph (`/sessions/:id` while running)
   - Filename: `03-ratchet-running.png`
   - At least 3-4 iterations done so you see green + red dots

## 4. Live loss curves on a single run (`/runs/:id` during training)
   - Filename: `04-live-loss.png`
   - Train + val loss curves both rendered

## 5. Dataset ingestion wizard step 1 (`/datasets/new`)
   - Filename: `05-ingest-step1.png`
   - "Upload file" tab selected

## 6. Dataset ingestion wizard step 2 (preview + schema mapping)
   - Filename: `06-ingest-step2.png`
   - After uploading sample.jsonl, fields detected, ready to save

## 7. Exports page with completed export (`/exports`)
   - Filename: `07-exports.png`
   - Q4_K_M tile visible with size + download link

## 8. Maintenance page (`/maintenance`)
   - Filename: `08-maintenance.png`
   - Disk usage table + cleanup action

## Capture tips

- macOS: `Cmd+Shift+5` → "Capture Selected Window" for clean shots
- Use Safari or Chrome at the default zoom (no scaling)
- Window width ~1280 px works well for README embedding
- After capture: `pngquant docs/screenshots/*.png --ext .png --force` to compress
