# Phase 1 Brief — Data Pipeline

> **Authoritative spec for the three Phase 1 subagents.** Read this in full plus [CLAUDE.md](CLAUDE.md) before writing any code. Single source of truth for scope, interfaces, acceptance, and prohibited changes.

---

## 0. Phase 1 at a glance

- **3 agents in parallel**, all Sonnet 4.6, one per branch (per [CLAUDE.md](CLAUDE.md) §4):
  - **Agent A** — Ingest (CDSE OAuth, STAC, Sentinel Hub, OData/S3) → `phase-1/ingest`
  - **Agent B** — Processor (pyroSAR/SNAP GRD + SLC InSAR pipelines) → `phase-1/processor`
  - **Agent C** — Jobs & Storage (Celery+Redis, content-hash cache, audit log) → `phase-1/jobs`
- Each agent works in its own git worktree; the orchestrator handles push, PRs, and merging to `develop`.
- Phase 0 (commit `3d7d7ed`) is the starting point. All Phase 0 deliverables are present and green.

---

## 1. Shared rules (apply to all three agents)

### 1.1 Frozen interfaces — do not modify

The following are locked. Any change is an **interface-change request**: stop, leave a note in this brief's "Interface change requests" section at the bottom (append-only), and ping the orchestrator.

**`shared/models.py`** (Pydantic v2):

```python
JobKind     = Literal["search", "fetch", "process", "analyze", "fuse"]
JobStatus   = Literal["queued", "running", "done", "error"]
AnomalyKind = Literal["new", "missing", "intensity_change"]

class AOI:       id: UUID; geometry: GeoJSON; created_at: datetime; label: str | None
class Scene:     id: UUID; platform: Literal["S1","S2"]; product_type: str;
                 sensing_start: datetime; sensing_end: datetime;
                 polarization: str | None; orbit_direction: Literal["ASCENDING","DESCENDING"] | None;
                 footprint: GeoJSON
class Job:       id: UUID; aoi_id: UUID; kind: JobKind; status: JobStatus;
                 created_at: datetime; started_at: datetime | None;
                 completed_at: datetime | None; error: str | None
class Baseline:  aoi_id: UUID; scene_ids: list[UUID]; median_db: float;
                 mad_db: float; n_obs: int; computed_at: datetime
class Anomaly:   id: UUID; aoi_id: UUID; scene_id: UUID;
                 bbox: tuple[float,float,float,float]; score: float;
                 kind: AnomalyKind; confirmed_omnibus: bool = False
```

**`shared/geo.py`** public surface:

```python
def bbox_from_geojson(geom: GeoJSON) -> BBox
def reproject(geom: GeoJSON, src_crs: str, dst_crs: str) -> GeoJSON
def tile_bounds(z: int, x: int, y: int) -> BBox
```

### 1.2 Branch & commit hygiene (CLAUDE.md §3)

- Branch from `develop`. Branch name exactly as listed (`phase-1/ingest|processor|jobs`).
- Conventional Commits (`feat:`, `test:`, `chore:`, `docs:`, `refactor:`, `sec:` for OPSEC-relevant changes).
- One logical change per commit; split if a diff touches more than 3 services.
- Commit message body MUST end with:
  ```
  Agent: phase-1-<ingest|processor|jobs>
  Model: claude-sonnet-4-6
  ```

### 1.3 Tooling gates (CLAUDE.md §7)

- Python 3.11, `uv`, `ruff` (line length 100), `mypy --strict` on `shared/` and your `services/<your>/src/`.
- `pytest` + `pytest-asyncio`; structured logging via `structlog`.
- Run `make ci` locally before declaring done. CI must pass.

### 1.4 No live network in CI (CLAUDE.md §6)

- HTTP under test: record with `vcr.py` cassettes under `tests/integration/<module>/cassettes/`.
- Never call CDSE / Sentinel Hub in CI. No real credentials in fixtures.
- A new outbound destination (beyond `*.dataspace.copernicus.eu` and Sentinel Hub) requires operator approval — stop and ask via "Interface change requests" below.

### 1.5 OPSEC defaults (CLAUDE.md §6)

- HTTP clients must honor `HTTPS_PROXY` / `ALL_PROXY` (httpx does this by default — don't break it).
- Structured logging redacts AOI coordinates unless `LOG_AOI=true`.
- No telemetry, no error-reporting SDKs, no analytics. Period.
- Containers run as non-root; no host networking, no `--privileged`.

### 1.6 Scope boundaries

- **Ingest** writes only under `services/ingest/` (plus its tests).
- **Processor** writes only under `services/processor/` (plus its tests). May add a new `snap` service entry in `docker-compose.yml`.
- **Jobs** writes under `services/api/src/jobs.py` (new), `services/worker/`, `shared/storage.py` (new), plus tests.
- `docker-compose.yml` is shared: **append-only**. If you need to rewrite an existing service entry, post in "Interface change requests".
- `pyproject.toml` workspace deps: add what you need to the existing dependency list (no new groups without justification).

### 1.7 Out of scope for Phase 1

- Analysis algorithms (baseline / omnibus / RGB composite) — Phase 2.
- Fusion logic — Phase 2.
- UI work — Phase 3.
- Threat model / docs polish — Phase 4.
- Any change to `shared/models.py` or `shared/geo.py`.

---

## 2. Agent A — Ingest (`phase-1/ingest`)

### 2.1 Scope

- **CDSE OAuth2** client-credentials flow with token caching + refresh-on-expiry.
  - Token endpoint: `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`.
  - Reads `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET` from env.
- **STAC search** against `https://catalogue.dataspace.copernicus.eu/stac/`.
  - Collections: `SENTINEL-1` and `SENTINEL-2`.
  - Filter by bbox (use `shared.geo.bbox_from_geojson`) + datetime range + product type (e.g. `IW_GRDH_1S`, `IW_SLC__1S`, `S2MSI2A`).
- **Sentinel Hub Processing API** client for cropped quicklook previews (used by the UI before committing to a full fetch). Reads `SH_INSTANCE_ID`.
- **OData / S3** client for full `.SAFE` downloads to `data/cache/<scene-id>/`.
- **SQLite persistence** for scene metadata; Alembic-managed migrations under `services/ingest/migrations/`. First migration creates a `scenes` table mirroring the `Scene` fields.

### 2.2 Interface that downstream code (Agent C's worker tasks) will call

```python
# services/ingest/src/client.py
from shared.models import AOI, Scene
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

async def search_scenes(
    aoi: AOI,
    start: datetime,
    end: datetime,
    platform: Literal["S1", "S2"],
    product_type: str | None = None,
) -> list[Scene]: ...

async def fetch_scene(scene_id: UUID) -> Path: ...
    # returns local path to the unpacked .SAFE directory

async def preview_scene(scene_id: UUID, bbox: tuple[float,float,float,float]) -> bytes: ...
    # returns PNG bytes for UI thumbnail
```

### 2.3 Deliverables

- `services/ingest/pyproject.toml` (workspace member) declaring runtime deps: `httpx`, `pystac-client`, `aiosqlite`, `alembic`, `sqlalchemy`, `vcrpy` (test).
- `services/ingest/src/{__init__,auth,stac,sh_processing,odata,db,client}.py`.
- `services/ingest/Dockerfile` (slim Python 3.11; non-root user).
- `services/ingest/migrations/versions/0001_initial.py` (Alembic).
- `tests/integration/ingest/test_{auth,stac,sh,odata}.py` + recorded cassettes under `tests/integration/ingest/cassettes/`.
- New env vars (if any) added to `.env.example` with comments.

### 2.4 Acceptance

- `pytest tests/integration/ingest -q` passes offline (cassettes only).
- `mypy --strict services/ingest/src` passes.
- `client.search_scenes` returns `Scene` instances that round-trip through Pydantic.

---

## 3. Agent B — Processor (`phase-1/processor`)

### 3.1 Scope

Two pyroSAR pipelines wrapping SNAP `gpt`. Verbatim from [CLAUDE.md](CLAUDE.md) §5:

**GRD pipeline** (default):

```
apply-orbit-file → thermal-noise-removal → border-noise-removal
  → calibration (σ⁰) → speckle filter (Refined Lee, 7×7)
  → range-doppler terrain correction → linear-to-dB → COG
```

**SLC InSAR pipeline** (opt-in per job):

```
TOPS-split → apply-orbit-file → back-geocoding
  → enhanced-spectral-diversity → interferogram formation
  → TOPS-deburst → topo-phase-removal → multilook
  → Goldstein filter → coherence → terrain correction → COG
```

- Both pipelines emit valid Cloud-Optimized GeoTIFFs; validate with `rio cogeo validate` in tests.
- **SNAP as a sidecar container**: add a `snap` service to `docker-compose.yml` (build context `services/processor/snap.Dockerfile`, ESA SNAP + JRE). Mount a host scratch dir (`./data/scratch:/scratch`). No host networking, no `--privileged`, non-root user.
- Processor worker invokes SNAP via subprocess (`docker exec` into the sidecar, or use the shared scratch volume — your call, document in a top-of-file comment with the trade-off).
- Concurrency cap: max 2 SLC jobs in parallel (encode as a semaphore or config constant `MAX_SLC_PARALLEL = 2`).

### 3.2 Interface that downstream code will call

```python
# services/processor/src/pipeline.py
from pathlib import Path

def process_grd(safe_dir: Path, out_dir: Path) -> Path: ...
    # returns path to the final COG

def process_slc_pair(safe_a: Path, safe_b: Path, out_dir: Path) -> Path: ...
    # returns path to the coherence COG
```

### 3.3 Deliverables

- `services/processor/pyproject.toml` declaring runtime deps: `pyroSAR`, `rasterio`, `rio-cogeo`, `numpy`.
- `services/processor/src/{__init__,graphs,pipeline,fixtures}.py`.
  - `graphs.py`: SNAP XML graph builders for GRD and SLC.
  - `pipeline.py`: the two top-level functions above.
  - `fixtures.py`: synthetic SAR data generators for tests.
- `services/processor/Dockerfile` (slim Python + pyroSAR).
- `services/processor/snap.Dockerfile` (ESA SNAP + JRE; large image — pin a specific SNAP version).
- New `snap` service entry appended to `docker-compose.yml`.
- `tests/unit/processor/test_graphs.py` — verifies graph XML structure and wiring against synthetic fixtures (NOT SAR physics).
- TODO comment in `docs/operator-guide.md` (create as a stub if it doesn't exist) noting that real-data smoke tests are a manual checklist item.

### 3.4 Acceptance

- `pytest tests/unit/processor -q` passes without SNAP installed locally (tests must mock the subprocess boundary; only the graph XML is verified for real).
- `mypy --strict services/processor/src` passes.
- `docker compose config` validates with the new `snap` sidecar.
- Output of `process_grd` against a synthetic fixture is a file that satisfies `rio cogeo validate` (use a tiny generated COG, not a SAR product).

---

## 4. Agent C — Jobs & Storage (`phase-1/jobs`)

### 4.1 Scope

- **Celery + Redis** orchestration. Use the existing `redis` service in `docker-compose.yml` (no changes to its entry).
- Replace the placeholder `services/worker/` with a real Celery worker (`celery -A app worker`). Update its Dockerfile to install runtime deps.
- **Job kinds** match the `JobKind` literal exactly: `search`, `fetch`, `process`, `analyze`, `fuse`. (`analyze` and `fuse` are stubs that raise `NotImplementedError("Phase 2")` — register them so Phase 2 agents can fill them in without rewiring.)
- **State machine**: `queued → running → done|error`. Persist Job rows in SQLite.
- **DAG**: `search → fetch → process → analyze → fuse`. Each step idempotent; results keyed by content hash (see storage interface).
- **API routes** in a new `services/api/src/jobs.py`:
  - `POST /jobs` (body: `{aoi_id, kind, params}`) → returns `Job`.
  - `GET /jobs/{id}` → returns `Job`.
  - `GET /jobs` → returns list (filterable by `aoi_id`, `status`).
  - Wire the router from `services/api/src/main.py`.
- **Audit log** feature-flagged on `AUDIT_LOG_ENABLED` (already in `.env.example`). When enabled, append JSONL to `data/audit.log` for: CDSE queries (via ingest hook), cache hits/misses, exports. Off by default.

### 4.2 Interface

```python
# shared/storage.py
from pathlib import Path

class LocalCache:
    def __init__(self, root: Path) -> None: ...
    def put(self, key: str, data: bytes | Path) -> Path: ...
    def get(self, key: str) -> Path | None: ...
    def has(self, key: str) -> bool: ...

    @staticmethod
    def hash_inputs(*parts: str | bytes) -> str: ...
```

`LocalCache` is consumed by ingest (raw scenes) and processor (derived COGs). Roots: `data/cache/` for raw, `data/derived/` for processed. Treat the interface above as frozen for Phase 1 (interface-change request if it must change).

### 4.3 Deliverables

- `shared/storage.py` (NEW).
- `services/worker/Dockerfile` (Celery), `services/worker/src/{__init__,app,tasks}.py`.
- `services/api/src/jobs.py` + router import + include in `main.py`.
- Alembic migration for the `jobs` table mirroring `Job` fields (if ingest's migration is in a separate Alembic env, that's fine; just don't conflict).
- `tests/unit/test_storage.py` (cache get/put/has, content-hash determinism).
- `tests/integration/test_jobs_dag.py` — fakes ingest and processor (monkeypatch the public functions from §2.2 and §3.2) and exercises `search → fetch → process` end-to-end through Celery's eager mode.

### 4.4 Acceptance

- `pytest tests/unit/test_storage.py tests/integration/test_jobs_dag.py -q` passes.
- `mypy --strict shared/storage.py services/api/src services/worker/src` passes.
- `curl -X POST localhost:8000/jobs -d '{"aoi_id":"...","kind":"search","params":{}}'` returns a queued Job (manual check; document in commit message).
- Audit log: with `AUDIT_LOG_ENABLED=true`, a search job appends a JSONL line; with the flag off, `data/audit.log` is not created.

---

## 5. Phase 1 acceptance (blocks merge to `main`)

Required before this phase is "done" per [CLAUDE.md](CLAUDE.md) §5:

1. The integration test on the **jobs** branch can: given a fixture AOI over a known archive scene, `search → fetch → process` end-to-end produces a valid COG. `rio cogeo validate` passes on the output. (Ingest and processor public functions may be monkey-patched with cassettes/fixtures — no live calls.)
2. All three branches squash-merge cleanly to `develop`.
3. `make ci` on `develop` is green after all three merges.

---

## 6. Prohibited (explicit)

- Modifying `shared/models.py`, `shared/geo.py`.
- Modifying another agent's service directory.
- Adding telemetry, analytics, crash-reporting, or any new outbound destination.
- Algorithm/analysis code (baselines, omnibus, RGB composite, fusion) — Phase 2.
- UI / React work — Phase 3.
- Touching `LICENSE`, `CLAUDE.md`, or this brief (except the "Interface change requests" section).
- Skipping pre-commit hooks (no `--no-verify`).
- Force-pushing or rewriting history on `develop` or `main`.

---

## 7. When to stop and ask (CLAUDE.md §8)

Stop and post in "Interface change requests" below (the orchestrator will arbitrate) if any of these come up:

- Need to change a frozen interface (`models.py`, `geo.py`, `LocalCache`, the function signatures in §2.2 / §3.2).
- Need a new outbound network destination.
- Two legitimate SAR processing recipes where the literature disagrees and the choice has user-visible impact.
- Any deviation from the OPSEC profile in [CLAUDE.md](CLAUDE.md) §6.
- A security issue in a dependency (commit with `sec:` prefix and tag).

---

## 8. Interface change requests (append-only)

> Agents append to this section. Orchestrator resolves.

_(none yet)_
