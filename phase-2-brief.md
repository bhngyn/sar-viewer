# Phase 2 Brief — Analysis & Fusion

> **Authoritative spec for the three Phase 2 subagents.** Read this in full plus [CLAUDE.md](CLAUDE.md) before writing any code. Single source of truth for scope, interfaces, acceptance, and prohibited changes.

---

## 0. Phase 2 at a glance

- **3 agents in parallel**, one per branch (per [CLAUDE.md](CLAUDE.md) §4):
  - **Agent D — Anomaly Detection** (Opus 4.7) → `phase-2/anomaly` → `services/analyzer/src/`
  - **Agent E — Multitemporal RGB Composite** (Sonnet 4.6) → `phase-2/composite` → `services/analyzer/src/rgb.py`
  - **Agent F — Fusion** (Sonnet 4.6, escalate to Opus 4.7 for any color-design question) → `phase-2/fusion` → `services/fusion/src/`
- Each agent works in its own git worktree; the orchestrator handles push, PRs, and merging to `develop`.
- Phase 1 (commit `79e0780`) is the starting point. All Phase 1 deliverables are present and green.

---

## 1. Shared rules (apply to all three agents)

### 1.1 Frozen interfaces — do not modify (except §1.1.1 below)

Same rules as phase-1-brief.md §1.1. Models in `shared/models.py` and `shared/geo.py` are frozen unless explicitly authorized below.

#### 1.1.1 Authorized model evolution (Baseline only)

The Phase 0 `Baseline` model was marked for Phase 2 evolution (see docstring in `shared/models.py`). Agent D is **authorized to extend** `Baseline` purely additively:

```python
class Baseline(BaseModel):
    aoi_id: UUID
    scene_ids: list[UUID]
    median_db: float          # KEEP — AOI-mean of per-pixel median (for quick summaries)
    mad_db: float             # KEEP — AOI-mean of per-pixel MAD
    n_obs: int
    computed_at: datetime
    median_db_cog: str | None = None   # NEW — path to per-pixel median COG (relative to data/)
    mad_db_cog: str | None    = None   # NEW — path to per-pixel MAD COG (relative to data/)
```

No other model changes are authorized in Phase 2. Anything else is an interface-change request (append to §8 below and ping the orchestrator).

### 1.2 Branch & commit hygiene (CLAUDE.md §3)

- Branch from `develop`. Branch name exactly as listed (`phase-2/anomaly|composite|fusion`).
- Conventional Commits. One logical change per commit; split if a diff touches more than 3 services.
- Commit message body MUST end with:
  ```
  Agent: phase-2-<anomaly|composite|fusion>
  Model: <claude-opus-4-7|claude-sonnet-4-6>
  ```
- For Agent F: any commit that introduces a color choice (LUT, alpha, blend mode) must cite the rationale in the body. If Opus 4.7 was consulted for a color decision, note it in the commit body (`Color-design model: claude-opus-4-7`).

### 1.3 Tooling gates (CLAUDE.md §7)

- Python 3.11, `uv`, `ruff` (line length 100), `mypy --strict` on `shared/` and your new `services/*/src/`.
- `pytest` + `pytest-asyncio`; structured logging via `structlog`.
- Run `make ci` locally before declaring done. CI must pass.
- Add your new package paths to the root `pyproject.toml`'s `[tool.mypy]` `files` list and to the CI workflow's `mypy` invocation in `.github/workflows/ci.yml` if applicable.

### 1.4 No network, no real SAR data, no large fixtures

- **Tests must run offline.** No CDSE, no Sentinel Hub, no GDAL HTTP drivers.
- **No SAR fixtures over 1 MB.** Generate synthetic numpy arrays in tests via `services/<svc>/src/fixtures.py` or `tests/unit/<module>/conftest.py`. Tiny synthetic COGs only; commit nothing to `tests/fixtures/` larger than 100 kB.
- For algorithms operating on rasters, write the synthetic input with `rasterio` and validate output with `rio cogeo validate`.

### 1.5 OPSEC defaults (CLAUDE.md §6)

- Unchanged from Phase 1. Structured logging redacts AOI coordinates unless `LOG_AOI=true`. No telemetry. Non-root containers.

### 1.6 Scope boundaries (read this carefully — three agents touch overlapping dirs)

- **Agent D (anomaly)** writes to:
  - `services/analyzer/src/{__init__,baseline,statistical,omnibus,detect}.py`
  - `services/analyzer/pyproject.toml` (new — workspace member)
  - `services/analyzer/Dockerfile` (new — slim Python 3.11, non-root)
  - `services/worker/src/tasks.py` — implement `task_analyze` (replace `NotImplementedError`).
  - `shared/models.py` — apply the **purely additive** Baseline change in §1.1.1. Nothing else.
  - `tests/unit/analyzer/test_{baseline,statistical,omnibus,detect}.py`
- **Agent E (composite)** writes to:
  - `services/analyzer/src/rgb.py` (new file only)
  - `tests/unit/analyzer/test_rgb.py`
  - Add deps to `services/analyzer/pyproject.toml` ONLY if Agent D's file doesn't exist yet on your branch — if it does, append; if not, create with the deps you need and document in commit body.
- **Agent F (fusion)** writes to:
  - `services/fusion/src/{__init__,fuse,colormap,watermark}.py` (new)
  - `services/fusion/pyproject.toml` (new — workspace member)
  - `services/fusion/Dockerfile` (new — slim Python 3.11, non-root)
  - `services/worker/src/tasks.py` — implement `task_fuse` (replace `NotImplementedError`).
  - `docs/architecture.md` — append a "Fusion color rationale" section (create the file as a stub if it doesn't exist).
  - `tests/unit/fusion/test_{fuse,colormap,watermark}.py`

**Coordination notes**

- `services/analyzer/pyproject.toml`: Agent D owns. Agent E should declare only the deps it actually needs; if its branch has to create the file, keep it minimal so the merge with Agent D is trivial.
- `services/analyzer/src/__init__.py`: Agent D owns. Agent E should NOT modify it (Agent D will re-export RGB from there at merge time).
- `services/worker/src/tasks.py`: Agent D modifies `task_analyze` only; Agent F modifies `task_fuse` only. Do not touch each other's task body.
- `docker-compose.yml` is **append-only**. If you need a new service entry, append. Do not modify existing entries without filing an interface change request in §8.

### 1.7 Out of scope for Phase 2

- UI / React / Leaflet / TiTiler config — Phase 3.
- Threat model, OPSEC review polish, operator guide — Phase 4.
- Any change to `shared/geo.py`, `shared/storage.py`, `services/ingest/*`, `services/processor/*`.
- Any change to `services/api/src/jobs.py` route handlers (the route layer already dispatches by JobKind string — analyzer/fusion plug in via `tasks.py`, not the API).
- Any new outbound network destination (CLAUDE.md §6).
- Live SAR/CDSE network calls in CI.

---

## 2. Agent D — Anomaly Detection (`phase-2/anomaly`, Opus 4.7)

### 2.1 Scope

Implement the two-stage anomaly detector described in [CLAUDE.md](CLAUDE.md) §5 Phase 2 Agent D:

**Stage 1 — baseline.**
Given an AOI and ≥10 prior σ⁰ (dB) COGs from the same relative orbit and polarization, compute per-pixel **median** and **MAD** rasters. Persist as two COGs (median_db.tif, mad_db.tif) under `data/derived/baselines/<aoi_id>/` and as a `Baseline` row.

**Stage 2 — statistical anomaly.**
For each new scene σ⁰ COG, compute the robust z-score
`z = (x - median) / (1.4826 * MAD)` per pixel, flag `|z| > 3.5`, then cluster flagged pixels with **DBSCAN**. `min_samples` scales with pixel resolution (default: `min_samples = max(5, round(50 / pixel_area_m2))`, with `eps = 2 * pixel_pitch_m`). Each cluster becomes an `Anomaly` with:

- `bbox` from cluster extent (lat/lon).
- `score` = max(|z|) within the cluster.
- `kind`:
  - `new` if cluster mean of (new − baseline_median) > +3 dB.
  - `missing` if cluster mean is < −3 dB.
  - `intensity_change` otherwise.

**Stage 3 — omnibus confirmation.**
Implement the Conradsen et al. (2016) per-pixel likelihood-ratio test for a time series of intensities. Reference (port to numpy, **not** GEE): https://developers.google.com/earth-engine/tutorials/community/detecting-changes-in-sentinel-1-imagery-pt-3

- Operate per cluster: extract the time series of σ⁰ for the cluster's centroid pixel (or median over the cluster) from the baseline + new scene stack.
- Compute the omnibus test statistic Q and the chi-squared p-value approximation (df depends on number of looks; for GRD use the equivalent number of looks from the SNAP metadata — default `enl=4.4` for GRDH).
- Set `Anomaly.confirmed_omnibus = True` iff `p < α` (default `α = 1e-4`, configurable).

**Explainability.**
Each `Anomaly` carries the time series of σ⁰ at the cluster centroid (list of (datetime, value) pairs) so the UI can render a sparkline. Add an attribute `time_series: list[tuple[datetime, float]] | None = None` — but per §1.1.1 only the additive Baseline change is authorized. Therefore: do **not** add `time_series` to the Pydantic `Anomaly` model. Instead, return time series alongside anomalies from `detect_anomalies()` as a sibling dict, and persist them separately in JSON under `data/derived/anomalies/<aoi_id>/<scene_id>.json`.

### 2.2 Public interface (called by `task_analyze`)

```python
# services/analyzer/src/baseline.py
from pathlib import Path
from uuid import UUID
from shared.models import Baseline

def build_baseline(
    aoi_id: UUID,
    cog_paths: list[Path],
    scene_ids: list[UUID],
    out_dir: Path,
) -> Baseline: ...
    # writes median_db.tif and mad_db.tif under out_dir; returns a Baseline.

# services/analyzer/src/detect.py
from datetime import datetime
from pathlib import Path
from uuid import UUID
from shared.models import Anomaly, Baseline

def detect_anomalies(
    aoi_id: UUID,
    new_scene_cog: Path,
    new_scene_id: UUID,
    baseline: Baseline,
    *,
    timeseries: list[tuple[datetime, Path]] | None = None,  # for omnibus
    z_threshold: float = 3.5,
    alpha: float = 1e-4,
    confirm_omnibus: bool = True,
    enl: float = 4.4,
) -> tuple[list[Anomaly], dict[str, list[tuple[str, float]]]]: ...
    # Returns (anomalies, time_series_by_anomaly_id).
    # time_series_by_anomaly_id maps str(anomaly.id) → [(iso_datetime, sigma0_db), ...]
```

### 2.3 Test plan (the high-stakes module — CLAUDE.md §5 explicit)

In `tests/unit/analyzer/`:

1. **`test_baseline.py`** — generate a synthetic 64×64 stack of 12 σ⁰ rasters with `numpy.random.default_rng(0).normal(loc=-12.0, scale=2.0)`. Assert:

   - `Baseline.n_obs == 12`.
   - `Baseline.median_db ≈ -12.0` within 0.5 dB.
   - `Baseline.mad_db ≈ 1.35` within 0.3 dB.
   - Output median_db.tif and mad_db.tif exist and `rio cogeo validate` passes.

2. **`test_statistical.py`** — inject a 5×5 bright (+10 dB) patch into one new scene; assert one `Anomaly` returned with `kind == "new"` and `bbox` enclosing the patch.

3. **`test_omnibus_step_change.py`** — synthesise a 30-step σ⁰ time series with a step change of +6 dB at t=15. Assert omnibus p-value < 1e-4.

4. **`test_omnibus_false_positive_rate.py`** — 1000 noise-only time series (Gamma-distributed with enl=4.4); assert empirical false-positive rate at α=1e-4 is < 5e-4 (5× the threshold; the chi² approximation isn't exact for short series).

5. **`test_omnibus_seasonal_no_change.py`** — synthesise σ⁰ with a sinusoidal component (±2 dB seasonal swing, period 12) and no step change; assert p > 0.01 (we want seasonal-but-stable to NOT trip).

6. **`test_detect_integration.py`** — end-to-end: baseline from 12 synthetic scenes + new scene with one bright patch → exactly one anomaly with `confirmed_omnibus=True`.

### 2.4 Wiring `task_analyze`

In `services/worker/src/tasks.py`, replace the `NotImplementedError` body of `task_analyze` with:

- Required `params` keys:
  - `aoi_id`: UUID string.
  - `new_scene_id`: UUID string.
  - `new_scene_cog`: path string (derived COG produced by `task_process`).
  - `baseline_cog_paths`: list of path strings (derived COGs from prior scenes).
  - `baseline_scene_ids`: list of UUID strings.
  - `timeseries_cog_paths`: optional list of path strings for omnibus.
  - `timeseries_dates`: optional list of ISO datetime strings (parallel to above).
  - `alpha`: optional float, default 1e-4.
- Use `LocalCache.hash_inputs(new_scene_id, *baseline_scene_ids, "analyze")` as the cache key; results live in `data/derived/` as JSON describing anomalies + sidecar baseline COGs.
- Persist anomalies as JSON to `data/derived/anomalies/<aoi_id>/<new_scene_id>.json`; persist the sibling time-series dict alongside.
- On success: `mark_job_done(job_id, result=<json path>)`.
- On failure: `mark_job_error(job_id, str(exc))`; re-raise so Celery records the exception.

### 2.5 Deliverables

- `services/analyzer/pyproject.toml` (new — workspace member). Runtime deps: `numpy>=1.26`, `scipy>=1.13`, `scikit-learn>=1.4`, `rasterio>=1.3`, `rio-cogeo>=5.0`.
- `services/analyzer/Dockerfile` (slim Python 3.11; non-root user).
- `services/analyzer/src/__init__.py`, `baseline.py`, `statistical.py`, `omnibus.py`, `detect.py`.
- `tests/unit/analyzer/test_{baseline,statistical,omnibus_step_change,omnibus_false_positive_rate,omnibus_seasonal_no_change,detect_integration}.py`.
- `services/worker/src/tasks.py` — `task_analyze` implemented.
- `shared/models.py` — purely additive `Baseline` extension per §1.1.1.
- `.github/workflows/ci.yml` — add `services/analyzer/src` to mypy invocation.
- Root `pyproject.toml` — add `services/analyzer/src` to `[tool.mypy] files`.

### 2.6 Acceptance

- `pytest tests/unit/analyzer -q` passes.
- `mypy --strict services/analyzer/src shared` passes.
- `task_analyze` runs end-to-end in Celery eager mode against a synthetic pipeline (extend the existing `tests/integration/test_jobs_dag.py` or add a new integration test). Output is a JSON file with valid `Anomaly` rows.
- `rio cogeo validate data/derived/baselines/<aoi>/median_db.tif` passes (assert in test).
- No new outbound destinations.

---

## 3. Agent E — Multitemporal RGB Composite (`phase-2/composite`, Sonnet 4.6)

### 3.1 Scope

Implement the standard physics-based RGB composite (Cian et al., IEEE doi 10.1109/JSTARS.2019.2904035) plus a simple change-RGB variant. This is the "Umbra-like" visual scan product; it will not match commercial SAR resolution but it makes S1 readable to a human.

### 3.2 Public interface

```python
# services/analyzer/src/rgb.py
from datetime import datetime
from pathlib import Path
from typing import Literal

def build_multitemporal_rgb(
    cog_paths: list[Path],
    dates: list[datetime],
    out_path: Path,
    *,
    mode: Literal["physics", "change"] = "physics",
    stretch_pct: tuple[float, float] = (2.0, 98.0),
) -> Path: ...
```

- **`physics` mode** (default): R = most recent VV (dB); G = median of prior VV; B = stddev across the stack. Requires ≥3 input scenes; raise `ValueError` if fewer.
- **`change` mode**: R = pre VV; G = post VV; B = (pre + post) / 2. Exactly 2 inputs; raise `ValueError` otherwise. Pre/post is determined by `dates`.
- Each channel is independently stretched to [0, 255] uint8 via the `stretch_pct` percentile range (default 2–98%), then written as a 3-band Cloud-Optimized GeoTIFF.
- Output COG must satisfy `rio cogeo validate`.
- Inputs are assumed already coregistered (the processor pipeline produces terrain-corrected COGs on a common grid). If shapes disagree, raise `ValueError`.

### 3.3 Test plan

In `tests/unit/analyzer/test_rgb.py`:

1. **Smoke (physics)** — 5 synthetic 32×32 VV-dB rasters; assert output is a 3-band uint8 COG of shape (3, 32, 32) and `rio cogeo validate` passes.
2. **Smoke (change)** — 2 inputs; assert output is 3-band uint8 COG; channel B equals the average of A and B.
3. **Stretch correctness** — input with known percentiles; assert post-stretch min/max are 0 and 255 respectively.
4. **Shape mismatch raises** — 2 rasters with different sizes; assert `ValueError`.
5. **Too few scenes for physics mode raises** — only 2 inputs; assert `ValueError`.

### 3.4 Deliverables

- `services/analyzer/src/rgb.py` (single new file).
- `tests/unit/analyzer/test_rgb.py`.
- If `services/analyzer/pyproject.toml` does not yet exist on your branch, create a **minimal** one declaring only `numpy>=1.26` and `rasterio>=1.3`. The orchestrator will merge with Agent D's version (which adds scipy/sklearn). Document the minimal scope in your commit body so the merge is trivial.

### 3.5 Acceptance

- `pytest tests/unit/analyzer/test_rgb.py -q` passes.
- `mypy --strict services/analyzer/src/rgb.py` passes.
- Output COGs satisfy `rio cogeo validate` (asserted in tests).

---

## 4. Agent F — Fusion (`phase-2/fusion`, Sonnet 4.6 + Opus 4.7 for color design)

### 4.1 Scope

Combine the S2 cloud-free basemap with SAR products into a single fused COG that the UI can serve via TiTiler. Four fusion modes from [CLAUDE.md](CLAUDE.md) §5 Phase 2 Agent F:

1. **side-by-side** — no fusion; the function returns the SAR path unchanged. The UI handles two viewports. We still expose a function for API uniformity.
2. **sar-on-s2** — SAR (σ⁰ dB) mapped via a perceptually uniform colormap (`gray` default; `viridis` allowed; **never `jet`**), alpha-blended over S2 RGB. Default alpha = 0.5.
3. **anomaly-highlight** — S2 RGB basemap + filled anomaly polygons. `new` → low-opacity red (#d62728, 25% fill, full-opacity outline); `missing` → low-opacity cyan (#17becf, 25% fill, full-opacity outline). Bbox-only is acceptable in v0.1 (cluster polygons are a Phase 3 nicety).
4. **change-rgb-on-s2** — the multitemporal RGB composite (from Agent E) alpha-blended over S2; alpha **strictly clamped ≤ 0.5** so basemap context remains.

All fused outputs:

- Are 3-band uint8 Cloud-Optimized GeoTIFFs (RGB).
- Carry an embedded "Sentinel-1 ~10m" watermark in the bottom-right of the raster (configurable via `WATERMARK` env var, default on). The watermark is a small text overlay drawn at write-time (use Pillow to render text into a tiny numpy patch, composite onto the bottom-right corner before encoding).
- Preserve the S2 CRS and transform.

### 4.2 Color-design rationale (must be documented)

Append a "Fusion color rationale" section to `docs/architecture.md` (create the file as a stub if missing). Cover:

- Why `gray` is the default LUT for SAR overlay (mono SAR is neutral, doesn't compete with S2 hues).
- Why `viridis` is allowed but `jet` is banned (perceptual uniformity, color-blind safety, no false hue boundaries).
- Why max alpha is 0.5 in `change-rgb-on-s2` (S2 context must remain readable).
- Why anomaly fills are 25% and outlines are 100% (a polygon that obscures the underlying basemap is harder to verify; the outline + low fill is a common ENG/OSINT convention).
- Cite Crameri et al. 2020 "The misuse of colour in science communication" for the rainbow-LUT ban.

**If you, Agent F, encounter a color decision the brief doesn't cover, escalate to Opus 4.7 by adding a comment in the PR description with the question and your two candidate answers. Do NOT silently pick one.**

### 4.3 Public interface

```python
# services/fusion/src/fuse.py
from pathlib import Path
from typing import Literal
from shared.models import Anomaly

def fuse(
    mode: Literal["side_by_side", "sar_on_s2", "anomaly_highlight", "change_rgb_on_s2"],
    s2_cog: Path,
    sar_cog: Path | None = None,
    rgb_cog: Path | None = None,
    anomalies: list[Anomaly] | None = None,
    out_path: Path = Path("fused.tif"),
    *,
    alpha: float = 0.5,
    colormap: Literal["gray", "viridis"] = "gray",
    watermark: bool = True,
) -> Path: ...
    # Returns out_path. For mode="side_by_side", returns sar_cog unchanged.
```

Per-mode required arguments:

- `side_by_side`: `sar_cog` required.
- `sar_on_s2`: `sar_cog` required.
- `anomaly_highlight`: `anomalies` required (may be empty list — produces a clean S2 COG with just a watermark).
- `change_rgb_on_s2`: `rgb_cog` required. `alpha` clamped to ≤ 0.5; raise `ValueError` if caller passes > 0.5.

Validate `colormap != "jet"` defensively; raise `ValueError("jet is banned for fusion — see docs/architecture.md")` if anyone tries.

### 4.4 Wiring `task_fuse`

In `services/worker/src/tasks.py`, implement `task_fuse`:

- Required `params` keys:
  - `mode`: one of the four literals.
  - `s2_cog_path`: path string.
  - `sar_cog_path`, `rgb_cog_path`: optional path strings (mode-dependent).
  - `anomalies`: optional list of dicts (deserialised to `Anomaly` instances).
  - `alpha`, `colormap`, `watermark`: optional overrides.
- Cache key: `LocalCache.hash_inputs(mode, s2_cog_path, sar_cog_path or "", rgb_cog_path or "")`.
- On success: `mark_job_done(job_id, result=str(out_path))`.
- Raise & `mark_job_error` on failure (same pattern as `task_process`).

### 4.5 Test plan

In `tests/unit/fusion/`:

1. **`test_colormap.py`** — `gray` and `viridis` map [0,1] → [0,255] RGB correctly; `jet` raises.
2. **`test_watermark.py`** — produced raster has non-zero pixels in the bottom-right 64×16 patch (text rendered).
3. **`test_fuse_sar_on_s2.py`** — synthetic 64×64 S2 RGB + SAR dB rasters; output is a 3-band uint8 COG; `rio cogeo validate` passes; alpha=0 yields output identical to S2 (modulo the watermark patch).
4. **`test_fuse_anomaly_highlight.py`** — S2 + 1 `Anomaly` with `kind="new"`; assert red channel inside bbox is elevated vs. outside.
5. **`test_fuse_change_rgb_on_s2.py`** — alpha=0.5 produces valid COG; alpha=0.6 raises `ValueError`.
6. **`test_fuse_jet_banned.py`** — `colormap="jet"` raises `ValueError`.
7. **`test_fuse_side_by_side.py`** — returns input path unchanged; does not create a new file.

### 4.6 Deliverables

- `services/fusion/pyproject.toml` (new — workspace member). Runtime deps: `numpy>=1.26`, `rasterio>=1.3`, `rio-cogeo>=5.0`, `pillow>=10.0`, `matplotlib>=3.8` (for colormap tables only — don't pull pyplot at runtime).
- `services/fusion/Dockerfile` (slim Python 3.11; non-root user).
- `services/fusion/src/__init__.py`, `fuse.py`, `colormap.py`, `watermark.py`.
- `tests/unit/fusion/test_{colormap,watermark,fuse_sar_on_s2,fuse_anomaly_highlight,fuse_change_rgb_on_s2,fuse_jet_banned,fuse_side_by_side}.py`.
- `services/worker/src/tasks.py` — `task_fuse` implemented.
- `docs/architecture.md` — "Fusion color rationale" section.
- Root `pyproject.toml` — add `services/fusion/src` to `[tool.mypy] files`.
- `.github/workflows/ci.yml` — add `services/fusion/src` to mypy invocation.

### 4.7 Acceptance

- `pytest tests/unit/fusion -q` passes.
- `mypy --strict services/fusion/src shared` passes.
- Output COGs satisfy `rio cogeo validate` (asserted in tests).
- `docs/architecture.md` color rationale section exists and references Crameri 2020.
- No new outbound destinations.

---

## 5. Phase 2 acceptance (blocks merge to `main`)

Required before this phase is "done" per [CLAUDE.md](CLAUDE.md) §5:

1. All three branches squash-merge cleanly to `develop`.
2. `make ci` on `develop` is green after all three merges.
3. The `analyze` and `fuse` Celery tasks no longer raise `NotImplementedError`.
4. The combined integration test (extend `tests/integration/test_jobs_dag.py`) exercises `search → fetch → process → analyze → fuse` end-to-end in Celery eager mode against synthetic inputs.
5. The operator-facing visual review (a known historical event → fused image showing the change) is captured as before/after screenshots in `docs/architecture.md`. This step is **manual** and is logged in the Phase 2 closeout — the orchestrator coordinates it after CI green.

---

## 6. Prohibited (explicit)

- Modifying `shared/geo.py`, `shared/storage.py`, `services/ingest/*`, `services/processor/*`.
- Modifying `shared/models.py` except the purely-additive Baseline extension in §1.1.1.
- Modifying `services/api/src/jobs.py` route handlers.
- Adding telemetry, analytics, crash-reporting, or any new outbound destination.
- UI / React work — Phase 3.
- Live SAR/CDSE network calls in CI.
- Test fixtures larger than 100 kB committed to git.
- Force-pushing or rewriting history on `develop` or `main`.
- Using `jet` or any other rainbow LUT anywhere.
- Skipping pre-commit hooks (no `--no-verify`).

---

## 7. When to stop and ask (CLAUDE.md §8)

Stop and post in §8 below (the orchestrator will arbitrate) if any of these come up:

- Need to change a frozen interface beyond §1.1.1.
- Two legitimate algorithm/color choices where the literature disagrees and the choice has user-visible impact. **Agent F: any color decision not covered in §4.2 lands here.**
- A security issue in a dependency (commit with `sec:` prefix and tag).
- Need a new outbound network destination.
- Any deviation from the OPSEC profile in [CLAUDE.md](CLAUDE.md) §6.

---

## 8. Interface change requests (append-only)

> Agents append to this section. Orchestrator resolves.

_(none yet)_
