"""Celery task definitions for the sar-viewer processing DAG.

Job DAG:  search → fetch → process → analyze → fuse

Each task is registered under its ``JobKind`` name so the API layer can
dispatch by kind string alone (e.g. ``search.apply_async(...)``).

Design notes
------------
- Tasks are **idempotent**: before doing work they check the cache via ``get()``
  and return early if the result is already cached.  This means re-submitting
  the same job is safe.
- Tasks call into the public interfaces defined in phase-1-brief.md §2.2 and
  §3.2.  Those modules may not be installed in the worker yet; the imports are
  deferred inside each task body so that the task *registration* does not fail
  at startup if optional service packages are absent.
- ``analyze`` and ``fuse`` raise ``NotImplementedError("Phase 2")`` — they are
  registered here so Phase 2 agents can fill them in without touching this
  file's Celery registration.
- Database operations (persisting Job state) use synchronous SQLAlchemy calls
  so Celery's default thread-pool executor does not require asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from services.worker.src.app import app
from services.worker.src.db import (
    mark_job_done,
    mark_job_error,
    mark_job_running,
)
from shared.storage import LocalCache

logger: structlog.BoundLogger = structlog.get_logger(__name__)

# Canonical cache roots.  ``data/`` is git-ignored and lives on the host
# filesystem; docker-compose mounts it into the container as a volume.
CACHE_ROOT = Path(os.environ.get("CACHE_ROOT", "data/cache"))
DERIVED_ROOT = Path(os.environ.get("DERIVED_ROOT", "data/derived"))

raw_cache = LocalCache(CACHE_ROOT)
derived_cache = LocalCache(DERIVED_ROOT)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously in a Celery task.

    Celery workers run in a plain thread pool, not an asyncio event loop.
    We create a new event loop for each call so that async ingest/processor
    functions work without modification.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.task(name="search", bind=True)
def task_search(self: Any, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Search CDSE STAC for scenes matching the AOI and time range.

    ``params`` keys (all required unless noted):
      - ``aoi_id``:        UUID string of the AOI.
      - ``aoi_geometry``:  GeoJSON dict of the AOI polygon.
      - ``start``:         ISO-8601 datetime string.
      - ``end``:           ISO-8601 datetime string.
      - ``platform``:      ``"S1"`` or ``"S2"``.
      - ``product_type``:  optional product type filter string.

    Returns the list of matching scenes serialised as dicts.
    """
    from shared.models import AOI

    mark_job_running(job_id)
    log = logger.bind(job_id=job_id, kind="search")

    try:
        # Import deferred: services/ingest may not be installed in every env.
        from services.ingest.src.client import search_scenes  # type: ignore[import-not-found]

        aoi = AOI(
            id=UUID(params["aoi_id"]),
            geometry=params["aoi_geometry"],
            created_at=datetime.now(UTC),
            label=params.get("label"),
        )
        start = datetime.fromisoformat(params["start"])
        end = datetime.fromisoformat(params["end"])
        platform = params["platform"]
        product_type = params.get("product_type")

        log.info("search_scenes.start", platform=platform)
        scenes = _run_async(search_scenes(aoi, start, end, platform, product_type))
        log.info("search_scenes.done", n_scenes=len(scenes))

        result = [s.model_dump(mode="json") for s in scenes]
        mark_job_done(job_id, result=json.dumps(result))
        return {"status": "done", "scenes": result}

    except Exception as exc:
        log.error("search.error", error=str(exc))
        mark_job_error(job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


@app.task(name="fetch", bind=True)
def task_fetch(self: Any, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Download a full .SAFE scene from CDSE OData/S3 to data/cache/.

    ``params`` keys:
      - ``scene_id``:  UUID string of the scene to fetch.

    The scene is stored under a content-hash key based on scene_id so that
    re-fetching the same scene is a no-op (idempotency).
    """
    from services.ingest.src.client import fetch_scene  # type: ignore[import-not-found]

    mark_job_running(job_id)
    log = logger.bind(job_id=job_id, kind="fetch")

    try:
        scene_id = UUID(params["scene_id"])
        cache_key = LocalCache.hash_inputs(str(scene_id), "safe")

        # Use get() rather than has() so the audit log captures cache hits/misses
        # when AUDIT_LOG_ENABLED=true (CLAUDE.md §6).
        cached_path = raw_cache.get(cache_key)
        if cached_path is not None:
            log.info("fetch.cache_hit", scene_id=str(scene_id))
            mark_job_done(job_id, result=str(cached_path))
            return {"status": "done", "path": str(cached_path)}

        log.info("fetch.start", scene_id=str(scene_id))
        safe_path = _run_async(fetch_scene(scene_id))
        stored = raw_cache.put(cache_key, safe_path)
        log.info("fetch.done", path=str(stored))
        mark_job_done(job_id, result=str(stored))
        return {"status": "done", "path": str(stored)}

    except Exception as exc:
        log.error("fetch.error", error=str(exc))
        mark_job_error(job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------


@app.task(name="process", bind=True)
def task_process(self: Any, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the GRD (or SLC) pipeline on a fetched .SAFE scene.

    ``params`` keys:
      - ``scene_id``:   UUID string (used to locate the cached .SAFE).
      - ``safe_path``:  path to the cached .SAFE directory.
      - ``pipeline``:   ``"grd"`` (default) or ``"slc"``.

    Output COGs are stored in data/derived/ under a hash of the inputs.
    """
    mark_job_running(job_id)
    log = logger.bind(job_id=job_id, kind="process")

    try:
        # Deferred import: services/processor may not be installed locally.
        from services.processor.src.pipeline import (  # type: ignore[import-not-found]
            process_grd,
            process_slc_pair,
        )

        scene_id = params["scene_id"]
        safe_path = Path(params["safe_path"])
        pipeline = params.get("pipeline", "grd")

        cache_key = LocalCache.hash_inputs(scene_id, pipeline)
        # Use get() so the audit log captures cache hits/misses (CLAUDE.md §6).
        cached = derived_cache.get(cache_key)
        if cached is not None:
            log.info("process.cache_hit", path=str(cached))
            mark_job_done(job_id, result=str(cached))
            return {"status": "done", "path": str(cached)}

        out_dir = DERIVED_ROOT / cache_key[:2] / cache_key
        out_dir.mkdir(parents=True, exist_ok=True)

        log.info("process.start", pipeline=pipeline, safe=str(safe_path))
        if pipeline == "slc":
            safe_b_str = params.get("safe_b_path")
            if safe_b_str is None:
                raise ValueError("SLC pipeline requires 'safe_b_path' param")
            cog_path = process_slc_pair(safe_path, Path(safe_b_str), out_dir)
        else:
            cog_path = process_grd(safe_path, out_dir)

        stored = derived_cache.put(cache_key, cog_path)
        log.info("process.done", cog=str(stored))
        mark_job_done(job_id, result=str(stored))
        return {"status": "done", "path": str(stored)}

    except Exception as exc:
        log.error("process.error", error=str(exc))
        mark_job_error(job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# analyze — Phase 2
# ---------------------------------------------------------------------------


@app.task(name="analyze", bind=True)
def task_analyze(self: Any, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a baseline (if not cached) and detect anomalies in the new scene.

    ``params`` keys:
      - ``aoi_id``:                UUID string of the AOI.
      - ``new_scene_id``:          UUID string of the scene being analysed.
      - ``new_scene_cog``:         path to the σ⁰ (dB) COG for that scene
                                   (produced by ``task_process``).
      - ``baseline_cog_paths``:    list of path strings — prior σ⁰ COGs.
      - ``baseline_scene_ids``:    list of UUID strings parallel to the above.
      - ``timeseries_cog_paths``:  optional list of path strings for omnibus.
      - ``timeseries_dates``:      optional list of ISO datetime strings,
                                   parallel to ``timeseries_cog_paths``.
      - ``alpha``:                 optional float, omnibus p-value threshold,
                                   default 1e-4.

    The result JSON is written to
    ``data/derived/anomalies/<aoi_id>/<new_scene_id>.json`` and the path is
    recorded as the job's ``result``.
    """
    # Deferred import: services/analyzer may not be available in every env.
    from services.analyzer.src.baseline import build_baseline  # type: ignore[import-not-found]
    from services.analyzer.src.detect import detect_anomalies  # type: ignore[import-not-found]

    mark_job_running(job_id)
    log = logger.bind(job_id=job_id, kind="analyze")

    try:
        aoi_id = UUID(params["aoi_id"])
        new_scene_id = UUID(params["new_scene_id"])
        new_scene_cog = Path(params["new_scene_cog"])
        baseline_cog_paths = [Path(p) for p in params["baseline_cog_paths"]]
        baseline_scene_ids = [UUID(s) for s in params["baseline_scene_ids"]]

        timeseries_cog_paths = [Path(p) for p in params.get("timeseries_cog_paths", []) or []]
        timeseries_dates_raw = params.get("timeseries_dates", []) or []
        timeseries_dates = [datetime.fromisoformat(s) for s in timeseries_dates_raw]
        if timeseries_cog_paths and len(timeseries_cog_paths) != len(timeseries_dates):
            raise ValueError("timeseries_cog_paths and timeseries_dates must have the same length")
        timeseries = list(zip(timeseries_dates, timeseries_cog_paths, strict=True)) or None
        alpha = float(params.get("alpha", 1e-4))

        # Build (or reuse) the baseline COGs.  We key on the sorted scene ids
        # so re-analysing a new scene against the same baseline is idempotent.
        baseline_key = LocalCache.hash_inputs(
            *sorted([s.hex for s in baseline_scene_ids]), "baseline"
        )
        baseline_dir = DERIVED_ROOT / "baselines" / baseline_key
        baseline_median = baseline_dir / "median_db.tif"
        baseline_mad = baseline_dir / "mad_db.tif"

        if baseline_median.exists() and baseline_mad.exists():
            log.info("analyze.baseline_cache_hit", dir=str(baseline_dir))
            from shared.models import Baseline as _Baseline

            baseline = _Baseline(
                aoi_id=aoi_id,
                scene_ids=baseline_scene_ids,
                # Scalars are best-effort recomputed below from a cheap read.
                median_db=float("nan"),
                mad_db=float("nan"),
                n_obs=len(baseline_scene_ids),
                computed_at=datetime.now(UTC),
                median_db_cog=str(baseline_median),
                mad_db_cog=str(baseline_mad),
            )
        else:
            log.info("analyze.baseline_build", n_scenes=len(baseline_cog_paths))
            baseline = build_baseline(
                aoi_id=aoi_id,
                cog_paths=baseline_cog_paths,
                scene_ids=baseline_scene_ids,
                out_dir=baseline_dir,
            )

        anomalies, ts_by_id = detect_anomalies(
            aoi_id=aoi_id,
            new_scene_cog=new_scene_cog,
            new_scene_id=new_scene_id,
            baseline=baseline,
            timeseries=timeseries,
            alpha=alpha,
        )

        # Persist as JSON sidecar for the UI / explainability sparklines.
        result_dir = DERIVED_ROOT / "anomalies" / str(aoi_id)
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / f"{new_scene_id}.json"
        result_path.write_text(
            json.dumps(
                {
                    "aoi_id": str(aoi_id),
                    "new_scene_id": str(new_scene_id),
                    "baseline_median_cog": baseline.median_db_cog,
                    "baseline_mad_cog": baseline.mad_db_cog,
                    "n_baseline_scenes": baseline.n_obs,
                    "alpha": alpha,
                    "anomalies": [a.model_dump(mode="json") for a in anomalies],
                    "time_series_by_anomaly_id": {
                        aid: [{"t": t, "sigma0_db": v} for (t, v) in ts]
                        for aid, ts in ts_by_id.items()
                    },
                },
                indent=2,
            )
        )

        log.info(
            "analyze.done",
            n_anomalies=len(anomalies),
            n_confirmed=sum(1 for a in anomalies if a.confirmed_omnibus),
            result=str(result_path),
        )
        mark_job_done(job_id, result=str(result_path))
        return {
            "status": "done",
            "path": str(result_path),
            "n_anomalies": len(anomalies),
        }

    except Exception as exc:
        log.error("analyze.error", error=str(exc))
        mark_job_error(job_id, str(exc))
        raise


# ---------------------------------------------------------------------------
# fuse — Phase 2 stub
# ---------------------------------------------------------------------------


@app.task(name="fuse", bind=True)
def task_fuse(self: Any, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """SAR ⊕ S2 basemap fusion.  Implemented in Phase 2."""
    mark_job_running(job_id)
    mark_job_error(job_id, "Phase 2")
    raise NotImplementedError("Phase 2")
