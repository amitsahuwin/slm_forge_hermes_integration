# Phase D Spec — Apache Ozone object storage

> **Status:** approved · **Date:** 2026-06-29 · **Owner:** Amit
> **Plan:** `docs/plans/2026-06-29-apache-ozone-storage.md`
> **Branch:** `feat/apache-ozone-storage` off `main` after Phase C.

---

## 1. Problem

Artifacts (datasets, run outputs, exported GGUF) live on the host
filesystem:

- 8 read/write call sites construct `pathlib.Path` directly against
  `/app/{data/datasets,runs,exports}` (container) and the corresponding
  host bind mounts.
- Upload handlers buffer entire archives into memory
  (`apps/api/routers/runs.py:253` calls `archive.file.read()`); a 1 GB
  upload blows the API's RSS.
- There is no abstraction; replacing storage requires editing every
  call site.
- Tenant isolation (Phase C) ends at the DB row — the disk layout is
  one shared tree, so a curious user with shell access could enumerate
  every artifact.

CLAUDE.md §25: "Never use local disk as the datastore — always a DB.
Large binaries → object storage (S3/blob) with references kept in the
DB."

---

## 2. Requirements

### R1 — Object-store abstraction

New package `apps/api/services/storage/`:

- `base.py`: `class ObjectStore(ABC)` with async methods
  ```python
  async def put(self, key: str, fileobj: AsyncIterable[bytes], *, content_type: str, metadata: dict[str,str] | None) -> ObjectMeta: ...
  async def get(self, key: str) -> AsyncIterator[bytes]: ...
  async def head(self, key: str) -> ObjectMeta | None: ...
  async def delete(self, key: str) -> None: ...
  async def list(self, prefix: str, limit: int = 1000) -> list[ObjectMeta]: ...
  async def presign_get(self, key: str, ttl_seconds: int) -> str: ...
  async def presign_put(self, key: str, ttl_seconds: int) -> str: ...
  ```
- `ozone.py`: `class OzoneObjectStore` using `aioboto3` S3 client.
- `local.py`: `class LocalObjectStore` against a filesystem root (used
  in tests, and as the 30-day disk fallback).
- `factory.py`: `def get_object_store(identity: Identity) -> ObjectStore`.
  Selects `Ozone | Local` via `SLM_FORGE_STORAGE` env. Wraps with
  `DiskFallbackStore` decorator if `SLM_FORGE_DISK_FALLBACK=true` and
  today < `2026-07-29`.
- `tenancy.py`: `async def ensure_tenant_bucket(identity)` —
  idempotent `HeadBucket`+`CreateBucket` on `slm-forge-{tenant_id}`.

### R2 — Key scheme

```
bucket: slm-forge-{tenant_id}
key:    {role}/{user_id}/{exports|runs|data}/{artifact_id}/{filename}
```

Examples:

```
slm-forge-acme/data_engineer/alice/runs/42/adapter/adapter_model.safetensors
slm-forge-acme/admin/admin@acme/exports/9/gguf/model-q4_k_m.gguf
slm-forge-globex/viewer/bob/data/clinical-notes-v3/train.jsonl
```

Role + user are captured at write time; subsequent role changes do
NOT migrate existing artifacts.

### R3 — Deployment (kind)

New directory `deploy/ozone/`:

- `values.yaml` — Helm override values for Apache's
  `ozone-helm-charts`. Sized for kind: 1 OM, 1 SCM, 3 DataNodes,
  1 S3 Gateway. PVCs use `local-path`.
- `kind-config.yaml` — single-node kind cluster with
  `extraPortMappings` exposing `s3g` on host port 9878 and `om` on 9874.
- `Makefile` targets:
  - `ozone-up` — create the kind cluster, install ozone-helm-charts via Helm.
  - `ozone-bootstrap` — create `slm-forge` volume + per-tenant buckets.
  - `ozone-down` — uninstall + delete cluster.
  - `ozone-status` — `kubectl get pods -n slm-forge-ozone` + `aws s3 ls`.

### R4 — Replace 8 call sites

Each call site listed in the parent plan changes to use
`storage.{put,get,head,delete}` via the factory. Public HTTP contract
unchanged.

Specifically:

| File:line                                  | Change                                          |
|--------------------------------------------|-------------------------------------------------|
| `apps/api/routers/runs.py:237-275`         | streaming multipart → storage.put per file      |
| `apps/api/routers/runs.py:268`             | drop `tarfile.extractall(dest)` (see streaming) |
| `apps/api/routers/datasets.py:66-86`       | `StreamingResponse(storage.get(...))`           |
| `apps/api/routers/datasets_detail.py:172`  | line-iterate `storage.get(...)`                 |
| `apps/api/routers/exports.py:185-242`      | `StreamingResponse(storage.get(...))`; drop 3-fallback |
| `apps/api/services/post_mortem.py:79,107`  | log reads via storage                           |
| `packages/exporter/pipeline.py:191,198-202,287,315` | read adapter via API; write outputs via API |

Workers continue to upload via the existing HTTP endpoints; the API is
the only process that talks to Ozone directly. (Single boundary,
easier auditing.)

### R5 — Streaming upload

`apps/api/routers/runs.py:237` switches from
`archive.file.read()` to a chunked async iterator:

```python
async def _chunks(uf: UploadFile, size: int = 64*1024):
    while True:
        chunk = await uf.read(size)
        if not chunk: break
        yield chunk

await storage.put(key, _chunks(archive), content_type="application/gzip")
```

For tar.gz contents, the API extracts members within the streaming
loop and `storage.put`s each member to its final key — no full-archive
materialization.

### R6 — 30-day disk fallback

`DiskFallbackStore` decorator (`factory.py`):

- on `head`/`get`: try Ozone first; on 404 and within the fallback
  window, fall through to `LocalObjectStore` rooted at the legacy disk
  paths;
- on `put`/`delete`: write/delete in Ozone only (legacy disk is read-only);
- emit a `WARN` log + Prom metric `slm_forge_disk_fallback_reads_total` per fallback;
- hardcoded sunset date `SLM_FORGE_DISK_FALLBACK_UNTIL=2026-07-29` —
  past it, the flag is ignored.

### R7 — Tests

- `tests/api/test_storage_local.py` — unit, `LocalObjectStore` against `tmp_path`.
- `tests/api/test_storage_ozone.py` — integration, skipped unless
  `SLM_FORGE_OZONE_TESTS=true`. Round-trip put/get/list/delete +
  presign.
- `tests/api/test_disk_fallback.py` — fallback decorator semantics.
- `tests/api/test_streaming_upload.py` — 500 MB synthetic tar.gz; RSS
  stays under 200 MB during upload.
- `tests/api/test_tenant_bucket_lifecycle.py` — bucket created on
  first login.
- `tests/e2e/test_two_tenant_e2e.py` — full stack: 2 tenants ×
  train→export→download in parallel; assert no key overlap in Ozone;
  assert DB rows isolated per Phase C.

---

## 3. Non-goals

- Replication / DR for Ozone (single-cluster only in this phase).
- Quotas per tenant in Ozone (deferred).
- Erasure coding tuning (default settings).
- Workers writing to Ozone directly (always API-routed).

---

## 4. Acceptance criteria

- All R7 tests written first; all pass (Ozone test gated on local cluster).
- `make ozone-up && make ozone-bootstrap` brings the cluster green
  (`kubectl get pods -n slm-forge-ozone` shows all `Running`).
- `aws --endpoint-url=http://localhost:9878 s3 ls` lists
  `slm-forge-acme` and `slm-forge-globex` buckets.
- Manual: log in as alice@acme, train a run, export to GGUF,
  download — artifact appears in
  `slm-forge-acme/{role}/alice/{runs,exports}/...` under Ozone.
- RSS of the API container stays flat during a 1 GB upload (verified
  via `docker stats` snapshot).
- `SLM_FORGE_DISK_FALLBACK=true` allows pre-Phase-D artifacts to
  download from disk; metric ticks.
- `uv run pytest -q` green; coverage ≥90% on `storage/*`.
