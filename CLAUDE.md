# CLAUDE.md — SAR-OSINT Investigation Tool

> **Purpose**: Operational guidance for Claude Code agents building a dockerized SAR imagery investigation tool for human rights researchers. This file is the single source of truth for architecture, conventions, agent orchestration, and acceptance criteria. Read this in full before starting any task.

---

## 0. Mission & Scope

Build a locally-runnable, dockerized OSINT tool that lets a human rights investigator:

1. **Draw an AOI** on a map (web UI, Leaflet-based).
2. **Search and retrieve** Sentinel-1 SAR scenes (and Sentinel-2 for the basemap) covering that AOI from the Copernicus Data Space Ecosystem (CDSE).
3. **Process SAR locally** — calibration, speckle filtering, terrain correction, and (when an SLC pair is available) interferometric coherence — using SNAP/pyroSAR.
4. **Detect anomalies** against a temporal baseline (statistical first-pass, omnibus second-pass confirmation).
5. **Fuse** the SAR + change product with a Sentinel-2 cloud-free basemap so an investigator sees both context and radar signal in the same view.
6. **Report** what the SAR baseline "normally" looks like vs. what is newly anomalous, with a timeline view.

### Out of scope (explicit)
- Real-time tasking, paid SAR providers (Umbra/ICEYE/Capella), automated object classification (we surface anomalies, humans interpret), and any cloud deployment. This is a local-first tool. If a future agent thinks "we should deploy this to AWS", **stop and ask the operator first.**

### Honest capability ceiling
Sentinel-1 is ~5×20m pixel spacing on GRD, ~2.3×14m on SLC. We can reliably surface: buildings, vehicle clusters (parking lots, convoys), ships >20m, aircraft on tarmac, earthworks, flooding, large-scale demolition, tent encampments, infrastructure changes. We **cannot** identify individuals, license plates, or small (<5m) objects. Every report the tool produces must carry a footer stating the resolution limit. Do not let the UI imply more than this.

---

## 1. Architectural Decisions (locked)

These were confirmed by the operator. Do not re-litigate without asking.

| Area | Decision |
|------|----------|
| Data access | **Hybrid**: Sentinel Hub Processing API for fast previews + small AOIs; CDSE OData/S3 for full SLC/GRD scenes for deep analysis. |
| Processing depth | **Heavy**: full local SLC pipeline (SNAP via pyroSAR), coherence/InSAR when a valid pair exists. |
| Anomaly detection | **Both**: median+MAD statistical first-pass for speed; Conradsen et al. omnibus test for confirmation. |
| UI | **FastAPI + React + Leaflet** (leaflet-draw for AOI, MapLibre-GL for COG overlays via XYZ tiles). |
| Basemap | **Sentinel-2 L2A cloud-free seasonal composite** built via Sentinel Hub mosaicking (least-cloudy pixel selection over a configurable window). |
| Tile serving | **TiTiler** (split packages: `titiler.core` + `titiler.mosaic` + `titiler.extensions`; the meta `titiler` package was deprecated late 2025). |
| Containerization | **docker-compose**, multi-service. Must run on a developer laptop (≥16GB RAM, ≥50GB disk). SLC processing is opt-in per job. |
| OPSEC | **Standard** (see §6). |
| Agent orchestration | **Adaptive**: 1 agent in Phase 0, 3 in Phase 1, scale to 5 only after Phase 1 is green. |
| Model selection | Opus 4.7 for science-heavy/algorithmic work; Sonnet 4.6 for everything else; no Haiku in this project. |

---

## 2. Repository Layout

```
sar-osint/
├── CLAUDE.md                    # this file
├── README.md                    # user-facing quickstart
├── THREAT_MODEL.md              # OPSEC, written in Phase 4
├── LICENSE                      # AGPL-3.0 (humanitarian-friendly, copyleft)
├── docker-compose.yml
├── docker-compose.override.yml.example
├── .env.example                 # CDSE creds, never commit real .env
├── .gitignore                   # see §3
├── .pre-commit-config.yaml
├── pyproject.toml               # workspace root, uv-managed
│
├── services/
│   ├── api/                     # FastAPI: jobs, AOIs, results
│   ├── ingest/                  # CDSE auth, STAC search, scene fetch
│   ├── processor/               # SNAP/pyroSAR pipelines
│   ├── analyzer/                # baseline stats + omnibus + RGB composite
│   ├── fusion/                  # SAR ⊕ S2 basemap blending → COG
│   ├── tiler/                   # TiTiler container
│   └── ui/                      # React + Leaflet frontend
│
├── shared/
│   ├── models.py                # Pydantic: AOI, Job, Scene, Anomaly, Baseline
│   ├── geo.py                   # bbox, GeoJSON, reprojection helpers
│   └── storage.py               # local cache interface
│
├── data/                        # gitignored; local cache lives here
│   ├── cache/                   # raw scenes
│   ├── derived/                 # processed COGs
│   └── audit.log                # (optional, off by default — see §6)
│
├── tests/
│   ├── unit/
│   ├── integration/             # uses recorded HTTP fixtures, never live CDSE
│   └── fixtures/                # small synthetic SAR rasters
│
└── docs/
    ├── architecture.md
    ├── sar-primer.md            # for the investigator, not the dev
    └── operator-guide.md
```

---

## 3. Git Workflow (non-negotiable)

### Branches
- `main` — protected, always deployable, signed commits required.
- `develop` — integration branch; agents merge feature branches here first.
- `phase-N/<module>` — agent working branches (e.g., `phase-1/ingest`).

### Commit hygiene
- **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `sec:`). The `sec:` prefix is reserved for OPSEC-relevant changes and triggers manual review.
- One logical change per commit. If an agent's diff touches more than 3 services, split it.
- Every commit message body must include `Agent: <agent-id>` and `Model: <claude-opus-4-7|claude-sonnet-4-6>` lines so we can audit which model wrote what.

### `.gitignore` essentials
```
.env
.env.*
!.env.example
data/
*.SAFE/
*.zip
*.tif
!tests/fixtures/*.tif
.venv/
__pycache__/
node_modules/
dist/
.pytest_cache/
.ruff_cache/
.mypy_cache/
audit.log
```

### Pre-commit hooks (set up in Phase 0)
1. `ruff` (lint + format Python)
2. `prettier` (JS/TS/JSON/YAML)
3. `mypy` (strict mode on `shared/` and `services/*/src/`)
4. `gitleaks` (secret scanning — fail on any match)

### PR rules
- Every agent opens a PR from its phase branch to `develop`. No direct pushes to `develop` or `main`.
- The lead Claude (the one running the orchestration) is the only one that merges to `main`, and only after all CI checks pass.
- Squash-merge phase branches; preserve the per-agent commit log in the squash commit body.

---

## 4. Agent Orchestration Plan

### Adaptive scaling rule
- Phase 0: **1 agent, sequential.** Foundation must be solid before any parallelism.
- Phase 1: **3 agents.** Independent modules, clean interfaces defined in Phase 0.
- Phase 2: **3 agents** (one of which is Opus). Scale to 5 only if Phase 1 lands clean and integration tests pass.
- Phase 3: **2 agents.** UI work has tight coupling; more agents create merge thrash.
- Phase 4: **1 agent, sequential.** Integration, hardening, docs.

### Coordination protocol
- Before spawning a phase's agents, the orchestrator writes a `phase-N-brief.md` in the repo root with: scope, interfaces (signatures + Pydantic models), acceptance tests, prohibited changes.
- Each subagent runs in its own git worktree under `worktrees/phase-N-<module>/` so parallel work doesn't collide.
- Subagents **must not** modify `shared/models.py` or `docker-compose.yml` without filing an interface-change request (a comment in `phase-N-brief.md`); the orchestrator arbitrates.
- Daily (or per-phase) sync: orchestrator runs `git fetch --all`, reviews each branch, comments on PRs, merges green ones.

### Model assignment (rationale)

| Task | Model | Why |
|------|-------|-----|
| Repo scaffolding, Docker, CI | Sonnet 4.6 | Pattern-matching work, large volume, well-known. |
| CDSE OAuth + STAC ingest | Sonnet 4.6 | API integration. Verify against live docs, no novel reasoning. |
| SNAP/pyroSAR processing graphs | Sonnet 4.6 | The hard part is knowing the recipe; pyroSAR docs give it. |
| **Anomaly detection algorithms** | **Opus 4.7** | Conradsen omnibus is non-trivial math; getting MAD baseline normalization right is the difference between "useful" and "misleading". |
| **Fusion logic + color science** | **Opus 4.7** | Picking SAR colorization that's both honest and readable is harder than it looks. |
| React UI + Leaflet integration | Sonnet 4.6 | Standard frontend work. |
| TiTiler config + tile auth | Sonnet 4.6 | Configuration. |
| **Threat model + OPSEC review** | **Opus 4.7** | Human rights context, adversarial thinking required. |
| Docs, READMEs, operator guide | Sonnet 4.6 | Writing. |

If an agent finds itself reaching for novel research, it should **pause and escalate** rather than guess.

---

## 5. Phased Implementation

### Phase 0 — Foundation (1 agent, Sonnet, sequential, ~half-day)

**Deliverables**
- `git init`, initial commit, `develop` branch created off `main`.
- `pyproject.toml` workspace using `uv` with Python 3.11.
- `docker-compose.yml` with five services stubbed (api, worker, redis, tiler, ui) — each prints "ok" and exits, but the compose file is valid and `docker compose config` passes.
- `shared/models.py` with these Pydantic v2 models (interfaces frozen here):
  - `AOI(id: UUID, geometry: GeoJSON Polygon, created_at, label: str|None)`
  - `Scene(id, platform: Literal["S1","S2"], product_type: str, sensing_start, sensing_end, polarization, orbit_direction, footprint)`
  - `Job(id, aoi_id, kind: Literal["search","fetch","process","analyze","fuse"], status, created_at, started_at, completed_at, error)`
  - `Baseline(aoi_id, scene_ids: list[UUID], median_db, mad_db, n_obs, computed_at)`
  - `Anomaly(id, aoi_id, scene_id, bbox, score, kind: Literal["new","missing","intensity_change"], confirmed_omnibus: bool)`
- `shared/geo.py` with `bbox_from_geojson`, `reproject`, `tile_bounds`.
- `.pre-commit-config.yaml` installed and working.
- `.env.example` listing `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`, `SH_INSTANCE_ID`.
- `README.md` with a one-page quickstart that the operator can follow.
- CI: a single GitHub Actions workflow (or local `make ci`) that runs ruff, mypy, pytest, and `docker compose build`.

**Acceptance**
- `make up` brings the stack up; `curl localhost:8000/health` returns `{"status":"ok"}`.
- `pytest` runs (zero tests yet is fine; the harness must work).
- Pre-commit blocks a commit containing the string `AKIA` (test that secret scanning works).

**Prohibited**
- Writing any SAR/algorithm code in Phase 0. Foundation only.

---

### Phase 1 — Data Pipeline (3 agents, Sonnet, parallel)

**Branches**: `phase-1/ingest`, `phase-1/processor`, `phase-1/jobs`

#### Agent A — Ingest (`services/ingest`)
- CDSE OAuth2 client credentials flow, token caching with refresh.
- STAC catalog search against `https://catalogue.dataspace.copernicus.eu/stac/` for `SENTINEL-1` and `SENTINEL-2` collections, filtered by AOI bbox + time range + product type.
- Sentinel Hub Processing API client for on-the-fly cropped previews (used by the UI before committing to a full fetch).
- OData/S3 client for full SAFE downloads.
- Output: scene metadata persisted to SQLite, raw products to `data/cache/`.
- Tests: HTTP fixtures recorded with `vcr.py`; never call CDSE in CI.

#### Agent B — Processor (`services/processor`)
- pyroSAR-based pipeline wrapping SNAP `gpt`. Two graphs:
  1. **GRD pipeline**: apply-orbit-file → thermal-noise-removal → border-noise-removal → calibration (σ⁰) → speckle filter (Refined Lee, 7×7) → range-doppler terrain correction → linear-to-dB → output COG.
  2. **SLC InSAR pipeline** (opt-in): TOPS-split → apply-orbit-file → back-geocoding → enhanced-spectral-diversity → interferogram formation → TOPS-deburst → topo-phase-removal → multilook → Goldstein filter → coherence → terrain correction → output COG.
- Both pipelines emit Cloud-Optimized GeoTIFFs (use `rio-cogeo` to validate).
- SNAP runs as a sidecar container (heavy: JRE + GPT) with a host-mounted scratch directory. Worker invokes via subprocess; concurrency capped at 2 SLC jobs in parallel (memory).
- Tests: synthetic SAR fixtures (random complex arrays packaged as fake SAFE structure) verify the graph wiring, not SAR physics. Real-data smoke test is a manual checklist item in `docs/operator-guide.md`.

#### Agent C — Jobs & Storage (`services/api/jobs.py`, `shared/storage.py`)
- Celery + Redis job orchestration. Job kinds: `search`, `fetch`, `process_grd`, `process_slc`, `analyze`, `fuse`. State machine: `queued → running → done|error`.
- Job DAG: `search → fetch → process → analyze → fuse`. Each step idempotent; results cached by content hash.
- `shared/storage.py`: local filesystem cache wrapper. Reads/writes for `data/cache/` and `data/derived/`. Content-hash-keyed so jobs are idempotent.
- Audit log is **off by default** in v0.1. If `AUDIT_LOG_ENABLED=true`, append plain JSONL to `data/audit.log` recording CDSE queries, cache hits/misses, and exports. (Off-by-default keeps things simple; the feature flag lets investigators enable it for accountability without re-architecting.)

**Phase 1 acceptance**
- Integration test: given a fixture AOI over a known archive scene, the system can search → fetch → process to a valid COG.
- All three branches merge cleanly to `develop`.
- `rio cogeo validate` passes on processor output.

---

### Phase 2 — Analysis & Fusion (3 agents; D is Opus, E and F are Sonnet)

**Branches**: `phase-2/anomaly`, `phase-2/composite`, `phase-2/fusion`

#### Agent D (Opus) — Anomaly Detection (`services/analyzer`)
- Baseline builder: given an AOI and ≥10 prior scenes (same relative orbit, same polarization), compute per-pixel median and MAD of σ⁰ (dB). Persist as `Baseline`.
- Statistical anomaly: for each new scene, compute robust z-score `(x - median) / (1.4826 * MAD)`; flag pixels with |z| > 3.5; cluster flagged pixels (DBSCAN, min-samples scaled to resolution) into `Anomaly` regions; classify each as `new` (bright in new, dim in baseline), `missing` (dim in new, bright in baseline), or `intensity_change`.
- Omnibus confirmation: implement Conradsen et al. (2016) per-pixel likelihood-ratio test for an arbitrary-length time series, with the chi-squared p-value approximation. Reference implementation: GEE community tutorial (port to numpy, not GEE). For each statistically-flagged anomaly cluster, run omnibus on the cluster's time series; only flip `confirmed_omnibus=True` if p < α (default 0.0001, configurable).
- Explainability: every `Anomaly` carries the time series of σ⁰ values that produced it, so the UI can show a sparkline.

**This is the highest-stakes module.** Wrong baselines produce false positives that mislead investigators. Required tests:
- Synthetic time series with injected step changes — must be detected.
- Synthetic time series with seasonal noise but no real change — must not be detected at α=0.0001.
- Speckle-only synthetic stack — false positive rate under α.

#### Agent E (Sonnet) — Multitemporal RGB Composite (`services/analyzer/rgb.py`)
- Implement the standard physics-based RGB composite (Cian et al. style): R = most recent VV (dB), G = median of prior VV (dB), B = standard deviation of VV (dB) over the stack. Normalize each channel to [0,255] using percentile stretches (2nd–98th). Output COG with 3 bands.
- Alternative composite for change detection: R = pre VV, G = post VV, B = (pre+post)/2. Configurable per job.
- This is the "Umbra-like" visual product. It will not match Umbra resolution, but it makes Sentinel-1 imagery scannable by a human.

#### Agent F (Sonnet/Opus split) — Fusion (`services/fusion`)
- **Opus for the color design**, Sonnet for the implementation. Color choices must be documented in `docs/architecture.md` with rationale, because they affect investigator perception.
- Input: Sentinel-2 true-color cloud-free composite + processed SAR product (either σ⁰ in dB, RGB composite, or anomaly overlay) + optional anomaly polygons.
- Output: a single fused COG and a TileJSON that the UI consumes.
- Fusion modes (user-selectable in the UI):
  1. **Side-by-side**: split-view (no fusion, just synced viewports). Default for cautious interpretation.
  2. **SAR-on-S2**: SAR as a semi-transparent overlay (alpha-blended; SAR luminance-mapped to a perceptually uniform colormap such as `cmocean.gray` or `viridis` — never `jet`).
  3. **Anomaly highlight**: S2 basemap + SAR-derived anomaly polygons outlined and filled with low-opacity red/cyan (new/missing).
  4. **Change RGB on S2**: the multitemporal RGB composite alpha-blended over the S2 basemap with a strict 50% max alpha so the basemap context remains visible.
- All fused outputs include a small visible "Sentinel-1 ~10m" stamp in a corner of the rendered tile (the watermark is configurable but defaults to on).

**Phase 2 acceptance**
- For a known historical event (operator picks one with public reporting), the tool produces a fused image that clearly shows the change. This is a manual review by the operator; capture before/after screenshots in `docs/architecture.md`.

---

### Phase 3 — UI (2 agents, Sonnet, parallel)

**Branches**: `phase-3/map`, `phase-3/results`

#### Agent G — Map & AOI (`services/ui/src/map`)
- React + Vite, TypeScript strict mode.
- Leaflet with `leaflet-draw` for polygon and rectangle AOI input. Constrain AOI area to a configurable max (default 500 km²) — larger areas are rejected with a clear message about quota and processing time.
- Base layers: OSM (default), Esri World Imagery (optional, requires user opt-in for the OPSEC reasons in §6), Sentinel-2 cloud-free composite served via our own TiTiler.
- Time range picker (date range, default last 90 days).
- "Search scenes" button → POSTs AOI to `/jobs/search` → polls until done → shows the list of available scenes with thumbnails (Sentinel Hub preview API).

#### Agent H — Results & Timeline (`services/ui/src/results`)
- Job dashboard: list of running and completed jobs, with status badges.
- Result view per AOI:
  - Fused tile overlay on the main map (XYZ from TiTiler).
  - Anomaly list panel (sortable by score, time). Click an anomaly → zoom and outline.
  - Sparkline of σ⁰ at the clicked anomaly's centroid.
  - Timeline ribbon along the bottom: every available scene as a tick, color-coded by whether it contains a confirmed anomaly.
- Export: "Download report" → renders a PDF (server-side via WeasyPrint) with the AOI, the fused image, the anomaly list, the time series, and a methods footer (resolution caveat, processing parameters, baseline scenes used).

**Phase 3 acceptance**
- An operator can complete the full loop in the browser: draw AOI → search → fetch → process → analyze → fuse → view → export PDF.
- No console errors in Chrome devtools during the loop.
- Lighthouse accessibility score ≥ 90.

---

### Phase 4 — Hardening & Documentation (1 agent, mixed, sequential)

- **Threat model (Opus)**: write `THREAT_MODEL.md` covering:
  - Adversary classes (state actors monitoring CDSE access patterns, local network sniffers, lost laptop, supply chain).
  - What the Standard profile actually mitigates vs. what it does not.
  - The deferred protections listed in §6, with notes on when an investigator should escalate to v0.2 work or use compensating controls (OS-level FDE, Tor, dedicated investigation machine).
  - Operational checklist for investigators: use full-disk encryption on the host, use a VPN or Tor for CDSE access, don't run on shared machines, lock the screen.
- **`docs/sar-primer.md` (Sonnet)**: a 4–6 page explainer for non-specialist investigators — what SAR can and cannot see, how to interpret colors, what a "false anomaly" looks like, and the resolution caveat repeated three times.
- **`docs/operator-guide.md` (Sonnet)**: install, first-run, common workflows, troubleshooting.
- **End-to-end smoke test (Sonnet)**: against a known fixture AOI in CI.
- **Release**: tag `v0.1.0`, build images, write release notes. (Cosign signing is deferred per §6.)

---

## 6. OPSEC Requirements (Standard profile)

This tool will be used to investigate atrocities, so we keep a small set of low-cost, high-value protections. We deliberately defer the harder protections (encryption at rest, AOI obfuscation, audit log, signed releases, air-gap mode) to a later release. The threat model document (Phase 4) calls these out as known residual risks so investigators can make informed decisions.

### What's in v0.1

1. **No telemetry**: zero analytics, zero crash reporting to third parties. The only outbound calls are to `*.dataspace.copernicus.eu` and (optionally) the user's chosen basemap CDN. If a future agent considers adding any analytics SDK or error-reporting service, **stop and ask the operator**.
2. **Secrets via `.env`**: never hardcoded, never committed. `.gitignore` excludes `.env*` (with `.env.example` whitelisted). Pre-commit `gitleaks` hook blocks accidental leaks.
3. **AOI redaction in local logs**: structured logging via `structlog` with a processor that replaces polygon coordinates with a stable hash by default. Plain coordinates only appear when `LOG_AOI=true` is set for debugging.
4. **Containers run unprivileged**: no `--privileged`, no host network, each service a non-root user. This is also good docker hygiene.
5. **Tor/VPN-compatible**: the HTTP client honors `HTTPS_PROXY` and `ALL_PROXY` environment variables (this is a free `httpx` default — no extra work). Documented in the operator guide as an option for investigators who want network-level protection.

### What's NOT in v0.1 (documented as residual risk)

The threat model in Phase 4 must enumerate each of these so investigators can decide:

- **AOI exposure to CDSE**: every search and fetch sends the exact bbox to EU-hosted CDSE infrastructure. Subject to EU lawful access and operator telemetry.
- **No encryption at rest**: the contents of `data/` are readable by anyone with disk access. Investigators should rely on OS-level full-disk encryption (FileVault, LUKS, BitLocker) and document this in `operator-guide.md` as a prerequisite.
- **No tamper-evident audit log**: an optional plain-text audit log is available (off by default).
- **No signed releases**: container images are not cosign-signed in v0.1.
- **No air-gap mode**: the tool requires network access to function.

These are explicitly v0.2+ candidates. If an investigator says they need any of them, that's a signal to revisit the profile — they're well-understood mitigations, just not free.

### Doesn't protect against (would not be solved by any reasonable profile)

- An adversary running CDSE/Copernicus infrastructure or compelling EU operators to disclose query logs.
- A compromised operator endpoint. We are not a substitute for OS-level security.

These belong in the threat model regardless of profile.

---

## 7. Coding Conventions

- **Python**: 3.11, `uv` for env management, `ruff` for lint+format (line length 100), `mypy --strict` on `shared/` and `services/*/src/`. Type-hint everything.
- **JS/TS**: TypeScript strict, `pnpm`, `prettier`, `eslint`. No `any` without a comment justifying it.
- **SQL**: SQLite for v0.1 (single-user tool); migrations via Alembic. Don't reach for Postgres until the operator asks.
- **Tests**: pytest with `pytest-asyncio`; aim for ≥70% line coverage on `shared/` and `services/analyzer` (the high-stakes module).
- **Logging**: structured JSON via `structlog`. Default log level `INFO`. AOI coordinates are redacted by a custom processor.
- **Errors**: never silently swallow CDSE errors; surface them to the UI with the original status code and a human-readable explanation.
- **Comments**: explain *why*, not *what*. SAR processing decisions especially need rationale in comments.

---

## 8. What to Do When Stuck

If an agent is uncertain about any of the following, **stop and ask the operator** (post a question in the PR description) rather than guessing:
- Any change to `shared/models.py` interfaces.
- Any new outbound network destination.
- Any deviation from the OPSEC profile defined in §6 (e.g., adding telemetry, weakening secret handling).
- Any choice between two legitimate SAR processing recipes where the literature disagrees.
- Whether to ship a feature that could mislead an investigator about the tool's limits.

If an agent finds a security issue (in our code or a dependency), commit with the `sec:` prefix and tag the orchestrator immediately.

---

## 9. Definition of Done (v0.1.0)

- [ ] All four phases merged to `main` via signed tagged release.
- [ ] `docker compose up` brings the stack up on a 16GB laptop with no errors.
- [ ] An operator can complete the full draw-AOI-to-PDF-report loop in under 30 minutes for a 100 km² AOI with 30 scenes.
- [ ] `THREAT_MODEL.md`, `sar-primer.md`, and `operator-guide.md` are complete and reviewed.
- [ ] CI passes on a fresh clone: `make ci` runs lint, types, tests, and `docker compose build`.
- [ ] No `TODO` or `FIXME` comments without an issue number.
- [ ] Resolution caveat appears in: the UI, every exported PDF, the README, and the operator guide.

---

## 10. Quick Reference

### Spinning up the orchestration (operator-side)
```bash
# Phase 0
claude code "Read CLAUDE.md and execute Phase 0. Use Sonnet 4.6."

# Phase 1 (after Phase 0 green)
claude code "Read CLAUDE.md, phase-1-brief.md. Spawn three subagents per the orchestration plan, one per branch. Use Sonnet 4.6 for all three."

# Phase 2 (after Phase 1 green and integration test passes)
claude code "Read CLAUDE.md, phase-2-brief.md. Spawn three subagents. Agent D uses Opus 4.7. Agents E and F use Sonnet 4.6 (F escalates color-design questions to Opus)."

# Phase 3
claude code "Read CLAUDE.md, phase-3-brief.md. Spawn two subagents. Sonnet 4.6 both."

# Phase 4
claude code "Read CLAUDE.md, phase-4-brief.md. Threat model in Opus 4.7; everything else Sonnet 4.6."
```

### Useful references (operator may want to read these)
- CDSE APIs: https://documentation.dataspace.copernicus.eu/APIs.html
- pyroSAR: https://pyrosar.readthedocs.io/
- Conradsen omnibus test (GEE tutorial port reference): https://developers.google.com/earth-engine/tutorials/community/detecting-changes-in-sentinel-1-imagery-pt-3
- TiTiler: https://developmentseed.org/titiler/
- Cian et al. multitemporal RGB: IEEE doi 10.1109/JSTARS.2019.2904035

---

*End of CLAUDE.md. Update this file when architectural decisions change; do not let it drift from reality.*
