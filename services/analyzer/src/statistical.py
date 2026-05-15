"""Robust-z + DBSCAN statistical anomaly detector.

Stage 2 of the anomaly pipeline (phase-2-brief.md §2.1):

1. Compute robust z-score per pixel: ``z = (x - median) / (1.4826 * MAD)``.
2. Flag pixels with ``|z| > z_threshold`` (default 3.5).
3. Cluster flagged pixels with DBSCAN; eps and min_samples scale with the
   image pixel pitch so a "cluster" corresponds to a real-world object size
   (≥ ~50 m² by default).
4. For each cluster, classify as ``new`` / ``missing`` / ``intensity_change``
   based on the sign and magnitude of the cluster-mean (new - median) in dB.

Output is a list of :class:`shared.models.Anomaly` instances. The clusters
themselves (pixel-coordinate lists) are returned alongside so the omnibus
confirmer (services/analyzer/src/omnibus.py) can pull a time series at the
cluster centroid.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import rasterio
import structlog
from rasterio.transform import xy as transform_xy
from sklearn.cluster import DBSCAN

from services.analyzer.src.baseline import MAD_SCALE
from shared.models import Anomaly, AnomalyKind, Baseline

log: structlog.BoundLogger = structlog.get_logger(__name__)

# Default detector knobs.  These are exposed as parameters on
# ``run_statistical`` so tests can tune them; the constants here document
# the design intent.
DEFAULT_Z_THRESHOLD: float = 3.5
DEFAULT_DELTA_DB_NEW: float = 3.0  # cluster-mean ΔdB above which we call "new"
DEFAULT_DELTA_DB_MISSING: float = -3.0


@dataclass(slots=True)
class StatCluster:
    """A pixel-coordinate description of one anomaly cluster.

    Carries everything the omnibus stage needs without re-parsing rasters.
    """

    anomaly_id: UUID
    pixel_rows: np.ndarray  # shape (n_pixels,) int
    pixel_cols: np.ndarray  # shape (n_pixels,) int
    centroid_row: int
    centroid_col: int
    bbox_geo: tuple[float, float, float, float]  # (minx, miny, maxx, maxy)
    kind: AnomalyKind
    score: float
    delta_db: float  # cluster mean (new - median), for human interpretation


def run_statistical(
    new_scene_cog: Path,
    new_scene_id: UUID,
    aoi_id: UUID,
    baseline: Baseline,
    *,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    delta_db_new: float = DEFAULT_DELTA_DB_NEW,
    delta_db_missing: float = DEFAULT_DELTA_DB_MISSING,
    dbscan_eps_px: float | None = None,
    dbscan_min_samples: int | None = None,
) -> tuple[list[Anomaly], list[StatCluster]]:
    """Run robust-z + DBSCAN on ``new_scene_cog`` against ``baseline``.

    Parameters
    ----------
    new_scene_cog:
        Path to the post-baseline σ⁰ (dB) COG to test.
    new_scene_id:
        UUID of that scene (for stamping ``Anomaly.scene_id``).
    aoi_id:
        UUID of the AOI (for stamping ``Anomaly.aoi_id``).
    baseline:
        :class:`Baseline` with ``median_db_cog`` and ``mad_db_cog`` set —
        i.e. produced by :func:`services.analyzer.src.baseline.build_baseline`.
    z_threshold:
        |z| threshold for a pixel to enter clustering. Default 3.5.
    delta_db_new / delta_db_missing:
        ΔdB cluster-mean thresholds for kind classification.
    dbscan_eps_px:
        DBSCAN eps in pixel units. Defaults to a value derived from the
        raster's pixel pitch (eps_m = 2 * pixel_pitch_m → eps_px = 2).
    dbscan_min_samples:
        DBSCAN min_samples. Defaults to a value derived from raster
        pixel area (min_samples = max(5, round(50 / pixel_area_m2))).

    Returns
    -------
    (anomalies, clusters)
        Parallel lists.  ``anomalies[i]`` corresponds to ``clusters[i]``;
        the omnibus stage walks ``clusters`` for time-series extraction.
    """
    if baseline.median_db_cog is None or baseline.mad_db_cog is None:
        raise ValueError(
            "statistical detector requires baseline.median_db_cog and "
            "baseline.mad_db_cog (per-pixel rasters); pass a Baseline built "
            "by services.analyzer.src.baseline.build_baseline"
        )

    new_arr, new_profile = _read_band_with_profile(new_scene_cog)
    median_arr, _ = _read_band_with_profile(Path(baseline.median_db_cog))
    mad_arr, _ = _read_band_with_profile(Path(baseline.mad_db_cog))

    if not (new_arr.shape == median_arr.shape == mad_arr.shape):
        raise ValueError(
            "shape mismatch between new scene and baseline rasters: "
            f"new={new_arr.shape}, median={median_arr.shape}, mad={mad_arr.shape}"
        )

    # Robust z-score.  Avoid division by zero when MAD is exactly 0 (flat
    # baseline pixels — e.g. open water in calm conditions); in that case
    # any deviation is treated as missing data because we can't compute a
    # meaningful z.
    with np.errstate(invalid="ignore", divide="ignore"):
        scale = MAD_SCALE * mad_arr
        z = (new_arr - median_arr) / scale
        z = np.where((scale == 0) | np.isnan(scale), np.nan, z)

    valid = np.isfinite(z)
    flagged = valid & (np.abs(z) > z_threshold)
    n_flagged = int(np.sum(flagged))
    log.info(
        "statistical.flagged",
        n_pixels=n_flagged,
        z_threshold=z_threshold,
    )

    if n_flagged == 0:
        return [], []

    # DBSCAN auto-tuning from raster pixel pitch.
    _pixel_pitch_m, pixel_area_m2 = _pixel_metrics(new_profile)
    if dbscan_eps_px is None:
        dbscan_eps_px = 2.0  # i.e. eps_m = 2 * pixel_pitch_m
    if dbscan_min_samples is None:
        dbscan_min_samples = max(5, round(50.0 / max(pixel_area_m2, 1e-6)))

    rows, cols = np.where(flagged)
    coords = np.column_stack([rows, cols]).astype(np.float32)

    # DBSCAN on pixel coordinates is O(n log n) with the ball-tree; for ≤ a
    # few hundred thousand flagged pixels this is fine on a laptop.  Larger
    # AOIs should pre-filter (e.g. dilation/erosion) — that's a Phase 3+
    # optimization.
    dbscan = DBSCAN(eps=dbscan_eps_px, min_samples=dbscan_min_samples)
    labels = dbscan.fit_predict(coords)

    n_clusters = int(labels.max()) + 1 if labels.size > 0 and labels.max() >= 0 else 0
    log.info(
        "statistical.clustered",
        n_clusters=n_clusters,
        eps_px=dbscan_eps_px,
        min_samples=dbscan_min_samples,
    )
    if n_clusters == 0:
        return [], []

    transform = new_profile["transform"]
    anomalies: list[Anomaly] = []
    clusters: list[StatCluster] = []
    for cluster_label in range(n_clusters):
        mask = labels == cluster_label
        cluster_rows = rows[mask]
        cluster_cols = cols[mask]

        # Cluster statistics.
        cluster_z = z[cluster_rows, cluster_cols]
        cluster_new = new_arr[cluster_rows, cluster_cols]
        cluster_med = median_arr[cluster_rows, cluster_cols]
        delta_db = float(np.mean(cluster_new - cluster_med))
        score = float(np.max(np.abs(cluster_z)))

        if delta_db > delta_db_new:
            kind: AnomalyKind = "new"
        elif delta_db < delta_db_missing:
            kind = "missing"
        else:
            kind = "intensity_change"

        bbox = _bbox_for_cluster(cluster_rows, cluster_cols, transform)
        # Sample the time series at the most-anomalous pixel in the cluster,
        # not the geometric centroid.  The centroid can land on a "boundary"
        # pixel where the new vs. baseline contrast is smaller; the omnibus
        # test is most discriminating where the signal is strongest.
        peak_idx = int(np.argmax(np.abs(cluster_z)))
        centroid_r = int(cluster_rows[peak_idx])
        centroid_c = int(cluster_cols[peak_idx])

        anomaly_id = uuid4()
        anomalies.append(
            Anomaly(
                id=anomaly_id,
                aoi_id=aoi_id,
                scene_id=new_scene_id,
                bbox=bbox,
                score=score,
                kind=kind,
                confirmed_omnibus=False,
            )
        )
        clusters.append(
            StatCluster(
                anomaly_id=anomaly_id,
                pixel_rows=cluster_rows,
                pixel_cols=cluster_cols,
                centroid_row=centroid_r,
                centroid_col=centroid_c,
                bbox_geo=bbox,
                kind=kind,
                score=score,
                delta_db=delta_db,
            )
        )

    return anomalies, clusters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_band_with_profile(path: Path) -> tuple[np.ndarray, dict[str, object]]:
    """Return (band1 as float32 with NaN nodata, profile dict)."""
    with rasterio.open(path) as src:
        band = src.read(1).astype(np.float32)
        if src.nodata is not None:
            band = np.where(band == src.nodata, np.nan, band)
        profile: dict[str, object] = dict(src.profile)
        profile["transform"] = src.transform
        profile["crs"] = src.crs
    return band, profile


def _pixel_metrics(profile: dict[str, object]) -> tuple[float, float]:
    """Return (pixel_pitch_m, pixel_area_m2) from a rasterio profile.

    Works for both projected (m) and geographic (deg) CRSs by falling back
    to a 10 m default for degrees (matches the Sentinel-1 GRD ground-range
    sample spacing) when we can't infer better.
    """
    transform = profile.get("transform")
    crs = profile.get("crs")
    if transform is None:
        return 10.0, 100.0
    # rasterio's Affine: (a, b, c, d, e, f) → a=pixel_width, e=pixel_height
    # ``transform`` is typed as object (we keep the profile dict permissive);
    # at runtime rasterio guarantees these attributes.
    pixel_width = abs(float(transform.a))  # type: ignore[attr-defined]
    pixel_height = abs(float(transform.e))  # type: ignore[attr-defined]

    # If the CRS is geographic, the units are degrees — assume Sentinel-1
    # nominal 10 m pitch as a sane default (better than treating degrees as
    # metres, which would give astronomically large min_samples).
    if crs is not None and getattr(crs, "is_geographic", False):
        return 10.0, 100.0

    pitch = (pixel_width + pixel_height) / 2.0
    return pitch, pixel_width * pixel_height


def _bbox_for_cluster(
    rows: np.ndarray,
    cols: np.ndarray,
    transform: object,
) -> tuple[float, float, float, float]:
    """Return (minx, miny, maxx, maxy) for a cluster of pixel indices.

    Uses ``rasterio.transform.xy`` so the box is in the dataset CRS (which
    may be projected or geographic). Caller is responsible for interpreting
    the units.
    """
    rmin = int(rows.min())
    rmax = int(rows.max())
    cmin = int(cols.min())
    cmax = int(cols.max())
    # transform_xy returns the centre of the pixel; for a bbox we want the
    # outer corner so we offset by ±0.5 pixel.
    minx, maxy = transform_xy(transform, rmin - 0.5, cmin - 0.5)
    maxx, miny = transform_xy(transform, rmax + 0.5, cmax + 0.5)
    # rasterio uses (row, col) -> (x, y); y decreases with row, so the
    # max-row corner has min-y. Order them to satisfy minx<=maxx, miny<=maxy.
    xs = sorted([float(minx), float(maxx)])
    ys = sorted([float(miny), float(maxy)])
    return (xs[0], ys[0], xs[1], ys[1])
