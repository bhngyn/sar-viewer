"""Top-level detect orchestrator.

Combines baseline + statistical + omnibus into the single public function
:func:`detect_anomalies` that ``services/worker/src/tasks.py::task_analyze``
calls.

Flow
----

1. Robust-z + DBSCAN over (new scene minus baseline) → candidate clusters
   with provisional kind classification.
2. For each candidate cluster, extract the time series of σ⁰ at the
   cluster centroid from the prior scenes (if provided) plus the new
   scene. Convert dB → linear and run the Conradsen omnibus LRT.
3. Mark ``Anomaly.confirmed_omnibus`` True when the omnibus p-value falls
   below ``alpha`` (default 1e-4 per phase-2-brief.md §2.1).

The function returns ``(anomalies, time_series_by_anomaly_id)``.  The
second element is a dict so :func:`task_analyze` can persist it as a
JSON sidecar (the :class:`Anomaly` Pydantic model has ``extra="forbid"``
and we deliberately do not extend it with a ``time_series`` field;
explainability data is carried alongside instead — see phase-2-brief.md
§2.1 Explainability).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import rasterio
import structlog

from services.analyzer.src.omnibus import DEFAULT_ENL_GRDH, db_to_linear, omnibus_test
from services.analyzer.src.statistical import (
    DEFAULT_DELTA_DB_MISSING,
    DEFAULT_DELTA_DB_NEW,
    DEFAULT_Z_THRESHOLD,
    StatCluster,
    run_statistical,
)
from shared.models import Anomaly, Baseline

log: structlog.BoundLogger = structlog.get_logger(__name__)


def detect_anomalies(
    aoi_id: UUID,
    new_scene_cog: Path,
    new_scene_id: UUID,
    baseline: Baseline,
    *,
    timeseries: list[tuple[datetime, Path]] | None = None,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    alpha: float = 1e-4,
    confirm_omnibus: bool = True,
    enl: float = DEFAULT_ENL_GRDH,
    delta_db_new: float = DEFAULT_DELTA_DB_NEW,
    delta_db_missing: float = DEFAULT_DELTA_DB_MISSING,
) -> tuple[list[Anomaly], dict[str, list[tuple[str, float]]]]:
    """Detect and confirm anomalies in ``new_scene_cog`` against ``baseline``.

    Parameters
    ----------
    aoi_id, new_scene_cog, new_scene_id, baseline:
        See :func:`services.analyzer.src.statistical.run_statistical`.
    timeseries:
        Optional list of ``(datetime, cog_path)`` pairs for the prior
        scenes used to build the baseline.  Required for omnibus
        confirmation; if absent, every anomaly's
        ``confirmed_omnibus`` stays ``False``.
    z_threshold:
        |z| threshold for the statistical first pass (default 3.5).
    alpha:
        Omnibus significance level. ``confirmed_omnibus`` is set True iff
        the p-value is strictly less than ``alpha``. Default ``1e-4`` per
        the brief.
    confirm_omnibus:
        If False, skip the omnibus stage entirely (statistical results
        only). Useful when no time series is available.
    enl:
        Equivalent number of looks passed through to
        :func:`services.analyzer.src.omnibus.omnibus_test`.
    delta_db_new, delta_db_missing:
        ΔdB thresholds for kind classification.

    Returns
    -------
    (anomalies, time_series_by_anomaly_id)
        ``anomalies`` is the list of Pydantic :class:`Anomaly` rows;
        ``time_series_by_anomaly_id`` maps the string form of each
        anomaly's UUID to ``[(iso_datetime, sigma0_db), ...]``.  When
        no time series was provided, every value is an empty list.
    """
    anomalies, clusters = run_statistical(
        new_scene_cog=new_scene_cog,
        new_scene_id=new_scene_id,
        aoi_id=aoi_id,
        baseline=baseline,
        z_threshold=z_threshold,
        delta_db_new=delta_db_new,
        delta_db_missing=delta_db_missing,
    )

    ts_by_id: dict[str, list[tuple[str, float]]] = {str(a.id): [] for a in anomalies}

    if not anomalies:
        return [], ts_by_id

    if not confirm_omnibus or not timeseries:
        # No omnibus stage — still attach time series at the new-scene
        # centroid if available (single point).
        for cluster in clusters:
            ts_by_id[str(cluster.anomaly_id)] = _read_centroid_series(
                new_scene_cog,
                cluster,
                stamp=None,
            )
        return anomalies, ts_by_id

    # Sort the time series chronologically so the omnibus stage sees a
    # consistent ordering and the explainability JSON reads naturally.
    sorted_ts = sorted(timeseries, key=lambda pair: pair[0])

    # Append the new scene at the end of the series so the omnibus sees the
    # latest observation alongside the baseline.  Its date is unknown to us
    # here — for the omnibus test the order doesn't matter (the LRT is
    # exchangeable), but for the sparkline we need a stamp; use the file
    # mtime as a pragmatic fallback if no datetime is in the metadata.
    # Reading rasterio's TIFFTAG_DATETIME would be marginally better but
    # adds fragility; the explainability JSON is human-reviewable.

    for cluster in clusters:
        series_db = _extract_centroid_series(sorted_ts, cluster, new_scene_cog)
        # series_db is a list of (datetime, dB) — convert to linear for the
        # LRT, run, and stash results.
        if len(series_db) < 2:
            ts_by_id[str(cluster.anomaly_id)] = [
                (dt.isoformat(), float(val)) for dt, val in series_db
            ]
            continue
        intensities = db_to_linear(np.array([val for _, val in series_db]))
        try:
            _, p_value = omnibus_test(intensities, enl=enl)
        except ValueError as exc:
            log.warning(
                "omnibus.skipped",
                anomaly_id=str(cluster.anomaly_id),
                reason=str(exc),
            )
            p_value = 1.0

        confirmed = p_value < alpha
        # Find the anomaly by id and update.  Pydantic v2 model is mutable
        # by default (no `frozen=True` set).
        for a in anomalies:
            if a.id == cluster.anomaly_id:
                a.confirmed_omnibus = bool(confirmed)
                break

        ts_by_id[str(cluster.anomaly_id)] = [(dt.isoformat(), float(val)) for dt, val in series_db]
        log.info(
            "omnibus.evaluated",
            anomaly_id=str(cluster.anomaly_id),
            p_value=p_value,
            confirmed=confirmed,
            n_samples=len(series_db),
        )

    return anomalies, ts_by_id


# ---------------------------------------------------------------------------
# Time-series extraction helpers
# ---------------------------------------------------------------------------


def _extract_centroid_series(
    sorted_ts: list[tuple[datetime, Path]],
    cluster: StatCluster,
    new_scene_cog: Path,
) -> list[tuple[datetime, float]]:
    """Walk the baseline COGs + new scene and pull σ⁰ at the cluster centroid."""
    out: list[tuple[datetime, float]] = []
    for stamp, path in sorted_ts:
        val = _read_pixel_db(path, cluster.centroid_row, cluster.centroid_col)
        if val is not None:
            out.append((stamp, val))
    new_val = _read_pixel_db(new_scene_cog, cluster.centroid_row, cluster.centroid_col)
    if new_val is not None:
        # Use the latest baseline stamp + 1 microsecond as a pragmatic stamp
        # for the new observation (we don't have its true datetime here).
        if sorted_ts:
            last_stamp = sorted_ts[-1][0]
            new_stamp = last_stamp.replace(microsecond=min(last_stamp.microsecond + 1, 999_999))
        else:
            new_stamp = datetime.fromtimestamp(0)
        out.append((new_stamp, new_val))
    return out


def _read_centroid_series(
    new_scene_cog: Path,
    cluster: StatCluster,
    stamp: datetime | None,
) -> list[tuple[str, float]]:
    """Single-sample fallback when no historical time series is provided."""
    val = _read_pixel_db(new_scene_cog, cluster.centroid_row, cluster.centroid_col)
    if val is None:
        return []
    iso = (stamp or datetime.fromtimestamp(0)).isoformat()
    return [(iso, float(val))]


def _read_pixel_db(path: Path, row: int, col: int) -> float | None:
    """Read a single pixel as a Python float in dB; ``None`` for NoData."""
    with rasterio.open(path) as src:
        if row < 0 or row >= src.height or col < 0 or col >= src.width:
            return None
        # Read just the window we need to avoid loading the whole raster.
        window = rasterio.windows.Window(col, row, 1, 1)
        val = src.read(1, window=window)[0, 0]
        if src.nodata is not None and float(val) == float(src.nodata):
            return None
        if not np.isfinite(val):
            return None
        return float(val)
