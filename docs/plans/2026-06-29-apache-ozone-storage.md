# Plan — Phase D: Apache Ozone object storage

> **Spec:** `docs/specs/2026-06-29-apache-ozone-storage.md`
> **Date:** 2026-06-29 · **Owner:** Amit
> **Branch:** `feat/apache-ozone-storage` off `main` after Phase C.
> **Parent plan:** `/Users/amitsahu/.claude/plans/hazy-stargazing-spindle.md`

---

## Red-team passes

### Pass 1 — kind networking
- **Concern:** the API runs in docker-compose, Ozone in a kind cluster.
  Two separate Docker networks. `host.docker.internal` works from the
  API container to `localhost:9878` on macOS, but kind on Linux exposes
  the port via the host loopback only.
- **Mitigation:** `make ozone-up` prints the exact endpoint URL to set
  as `SLM_FORGE_OZONE_S3_ENDPOINT`. Linux falls back to
  `http://172.17.0.1:9878`. Document both in `.env.example`.

### Pass 2 — S3 compatibility edge cases
- **Concern:** Ozone advertises S3 compatibility but presigned URLs use
  AWS Signature V4; older Ozone versions had quirks with virtual-hosted
  style.
- **Mitigation:** `OzoneObjectStore` forces path-style addressing and
  V4 signing. `test_storage_ozone.py::test_presign_roundtrip` verifies
  on day one. If presign fails, downgrade to streaming-through-API
  (the API path is the supported one anyway).

### Pass 3 — streaming-upload correctness
- **Concern:** chunked uploads on tar.gz might corrupt the tarball if a
  chunk boundary lands on a tar block header.
- **Mitigation:** we extract within the streaming loop via
  `aiofiles.tarfile` (or `tarfile.open(mode="r|gz")` streaming
  mode) — tar's streaming format is designed to be processed without
  seeking. The verified golden: `test_streaming_upload.py` round-trips
  a 500 MB synthetic archive and asserts checksum equality on every
  member.

### Pass 4 — disk fallback poisoning
- **Concern:** an attacker who can write to the legacy disk path can
  serve poisoned artifacts during the fallback window.
- **Mitigation:** fallback only on `head/get` and only for paths the DB
  already knows about (we look up `Export.gguf_path` etc., not arbitrary
  disk paths). Mounts are owned by the API user; container is
  non-root. Past sunset date the flag is ignored.

### Pass 5 — Ozone resource use on kind
- **Concern:** 3 DataNodes + OM + SCM + S3G on kind eats ~2 GB RAM.
- **Mitigation:** values.yaml sets JVM heaps minimally (256m per
  service). Test environments use 1 DataNode. Document the
  requirement in `README.md`.

### Pass 6 — clean (target)
- Single boundary: API → Ozone. Workers never touch Ozone directly.
- Streaming everywhere — no in-memory archive materialization.
- Disk fallback is bounded by date + DB lookup, not free-form.

---

## Implementation steps

### Step 0 — bring up Ozone (manual gate)

```bash
make ozone-up
make ozone-bootstrap     # creates volume + 2 demo tenant buckets
make ozone-status        # all pods Running
```

This must succeed before any code changes land. If kind networking
needs work, fix here.

### Step 1 — Tests (RED)

Create the six test files in spec §R7. Run them; all RED.

### Step 2 — R1: storage abstraction

`apps/api/services/storage/{base.py,local.py}` first (synchronous-ish
implementations a test can grip). Then `ozone.py`, then `factory.py`,
then `tenancy.py`.

### Step 3 — R3 deployment files

`deploy/ozone/values.yaml`, `deploy/ozone/kind-config.yaml`,
`scripts/ozone_bootstrap.py`. Makefile targets.

### Step 4 — R4 + R5: call-site replacement

Walk the 8 sites in spec §R4. After each one, run the corresponding
test (e.g. `tests/api/test_exports_download.py` if it exists, else add
one) to confirm parity.

For the streaming upload, replace `archive.file.read()` with the
chunked async iterator from spec §R5.

### Step 5 — R6: disk fallback

`DiskFallbackStore` decorator + factory wiring + the Prometheus
metric.

### Step 6 — verify

```bash
make ozone-up && make ozone-bootstrap
SLM_FORGE_OZONE_TESTS=true uv run pytest tests/api/test_storage_ozone.py -q
SLM_FORGE_DISK_FALLBACK=true uv run pytest tests/api/test_disk_fallback.py -q
uv run pytest tests/api/test_streaming_upload.py -q
uv run pytest tests/e2e/test_two_tenant_e2e.py -q
uv run pytest -q   # full suite
uv run ruff check --fix
uv run mypy apps packages
```

### Step 7 — manual end-to-end

Per spec §4. Use `docker stats` during a 1 GB upload to confirm flat RSS.

### Step 8 — commit + PR

PR title: `feat: Apache Ozone object storage (Helm/kind deploy + streaming)`.

PR body includes:
- a runbook for bringing up Ozone locally,
- a migration note: existing on-disk artifacts remain readable via
  fallback until 2026-07-29,
- a rollback plan: `SLM_FORGE_STORAGE=local` reverts to disk-only mode
  without redeploy.

---

## Files modified

- `deploy/ozone/values.yaml` (new)
- `deploy/ozone/kind-config.yaml` (new)
- `scripts/ozone_bootstrap.py` (new)
- `apps/api/services/storage/__init__.py` (new)
- `apps/api/services/storage/base.py` (new)
- `apps/api/services/storage/ozone.py` (new)
- `apps/api/services/storage/local.py` (new)
- `apps/api/services/storage/factory.py` (new)
- `apps/api/services/storage/tenancy.py` (new)
- `apps/api/routers/runs.py` (streaming upload + storage)
- `apps/api/routers/datasets.py` (storage)
- `apps/api/routers/datasets_detail.py` (storage)
- `apps/api/routers/exports.py` (storage)
- `apps/api/services/post_mortem.py` (storage)
- `packages/exporter/pipeline.py` (read/write via API)
- `Makefile` (`ozone-*` targets)
- `.env.example` (`SLM_FORGE_OZONE_*`, `SLM_FORGE_STORAGE`, `SLM_FORGE_DISK_FALLBACK_*`)
- `pyproject.toml` (add `aioboto3`)
- `tests/api/test_storage_local.py` (new)
- `tests/api/test_storage_ozone.py` (new)
- `tests/api/test_disk_fallback.py` (new)
- `tests/api/test_streaming_upload.py` (new)
- `tests/api/test_tenant_bucket_lifecycle.py` (new)
- `tests/e2e/test_two_tenant_e2e.py` (new)
- `README.md` (Ozone bring-up section)
- `docs/specs/2026-06-29-apache-ozone-storage.md`
- `docs/plans/2026-06-29-apache-ozone-storage.md`
- `release/PR-4.md` (new)

## Definition of Done

- [ ] Spec + plan committed
- [ ] All 6 test files green (Ozone test gated)
- [ ] Coverage ≥90% on `apps/api/services/storage/*`
- [ ] `make ozone-up` + `ozone-bootstrap` succeed on a clean machine
- [ ] Manual end-to-end on 2 tenants: artifacts isolated in Ozone
- [ ] Streaming upload verified flat RSS at 1 GB
- [ ] Disk fallback verified for legacy artifacts
- [ ] `ruff` + `mypy` + `npm run build` green
- [ ] PR-4.md + README Ozone section
- [ ] Rollback: `SLM_FORGE_STORAGE=local` confirmed working
