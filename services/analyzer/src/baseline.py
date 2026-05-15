"""Per-pixel baseline raster builder.

Given a stack of sigma-0 (dB) Cloud-Optimized GeoTIFFs from the same AOI,
relative orbit, and polarization, compute:

* per-pixel **median** raster — robust central tendency
* per-pixel **MAD** raster — robust dispersion (median absolute deviation)

Both are written as single-band float32 COGs into ``out_dir`` so the
statistical detector (services/analyzer/src/statistical.py) can read them.

The median and MAD choice (vs. mean + std) is deliberate: SAR backscatter
distributions are heavy-tailed in dB space, and a single flooded scene in
the baseline would skew a mean/std baseline enough to mask the next change.
The robust scale 1.4826 (used in :mod:`~.statistical`) makes MAD comparable
to a Gaussian sigma when the underlying noise is approximately normal.

Design notes
------------
* Inputs may be **mosaicked partial overlaps** — pixels that are NoData in
  some scenes still get a baseline if at least 3 valid observations remain.
  Fewer than 3 valid obs at a pixel yields NoData in both output rasters.
* Inputs must already share a CRS, transform, and shape (the processor
  pipeline produces terrain-corrected COGs on a common grid). Shape
  mismatches raise ``ValueError``.
* The output COGs are validated by ``rio-cogeo`` in the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import rasterio
import structlog
from rasterio.io import DatasetReader
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles

from shared.models import Baseline

log: structlog.BoundLogger = structlog.get_logger(__name__)

# The robust scale factor that makes MAD comparable to a Gaussian sigma:
#     1.4826 ≈ 1 / Phi^{-1}(0.75)
# Documented here once; referenced by both :mod:`baseline` and
# :mod:`statistical`.
MAD_SCALE: float = 1.4826

# Minimum number of valid observations per pixel before we emit a baseline
# value.  Per the brief, a baseline needs ≥10 scenes overall; per-pixel we
# tolerate gaps but require ≥3 valid samples (1 sample = no MAD; 2 samples =
# unstable MAD estimate).
MIN_PIXEL_OBS: int = 3

# NoData sentinel for the float32 outputs.
_NODATA: float = float("nan")


def build_baseline(
    aoi_id: UUID,
    cog_paths: list[Path],
    scene_ids: list[UUID],
    out_dir: Path,
) -> Baseline:
    """Build the per-pixel median + MAD baseline rasters for an AOI.

    Parameters
    ----------
    aoi_id:
        UUID of the AOI these scenes cover.
    cog_paths:
        Paths to sigma-0 (dB) COGs.  Must have identical CRS, transform,
        and shape; mismatches raise ``ValueError``.
    scene_ids:
        UUIDs of the scenes that produced ``cog_paths``, parallel.
    out_dir:
        Directory to write ``median_db.tif`` and ``mad_db.tif`` into.
        Created if it does not exist.

    Returns
    -------
    Baseline
        Pydantic model carrying:

        * AOI-mean scalar summaries (``median_db``, ``mad_db``) over all
          valid pixels — useful for at-a-glance comparisons.
        * Paths to the per-pixel COGs (``median_db_cog``, ``mad_db_cog``)
          for the per-pixel detector.

    Raises
    ------
    ValueError
        If ``cog_paths`` is empty, the lengths of ``cog_paths`` and
        ``scene_ids`` differ, or input rasters disagree on shape/CRS.
    """
    if not cog_paths:
        raise ValueError("baseline requires at least one input COG")
    if len(cog_paths) != len(scene_ids):
        raise ValueError(
            f"cog_paths and scene_ids must have the same length "
            f"(got {len(cog_paths)} vs {len(scene_ids)})"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    # Read the stack.  We load everything into memory at float32 — for the
    # 16 GB reference laptop and typical AOI sizes (≤500 km²; ~5 megapixels at
    # 10 m posting) the stack fits comfortably.  Larger AOIs would need a
    # tiled pass; that's a Phase 3+ concern.
    stack, profile = _read_stack(cog_paths)
    log.info(
        "baseline.read_stack",
        n_scenes=stack.shape[0],
        height=stack.shape[1],
        width=stack.shape[2],
    )

    # Per-pixel median / MAD via numpy.nanmedian.  np.nanmedian returns NaN
    # for all-NaN pixels with a RuntimeWarning — we suppress that warning
    # because we explicitly handle NaN-coverage below.
    with np.errstate(invalid="ignore"):
        median = np.nanmedian(stack, axis=0).astype(np.float32)
        deviations = np.abs(stack - median[np.newaxis, :, :])
        mad = np.nanmedian(deviations, axis=0).astype(np.float32)

    n_valid = np.sum(~np.isnan(stack), axis=0)
    insufficient = n_valid < MIN_PIXEL_OBS
    median[insufficient] = _NODATA
    mad[insufficient] = _NODATA

    median_path = out_dir / "median_db.tif"
    mad_path = out_dir / "mad_db.tif"
    _write_cog(median, profile, median_path)
    _write_cog(mad, profile, mad_path)

    valid_mask = ~np.isnan(median)
    if not np.any(valid_mask):
        aoi_median = float("nan")
        aoi_mad = float("nan")
    else:
        aoi_median = float(np.mean(median[valid_mask]))
        aoi_mad = float(np.mean(mad[valid_mask]))

    return Baseline(
        aoi_id=aoi_id,
        scene_ids=scene_ids,
        median_db=aoi_median,
        mad_db=aoi_mad,
        n_obs=len(cog_paths),
        computed_at=datetime.now(UTC),
        median_db_cog=str(median_path),
        mad_db_cog=str(mad_path),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_stack(cog_paths: list[Path]) -> tuple[np.ndarray, dict[str, object]]:
    """Read all COGs into a (N, H, W) float32 stack with NaN for NoData.

    Verifies that shape and transform agree across the stack.  The returned
    profile is taken from the first input and is used as the template for
    writing the output COGs (preserving CRS / transform).
    """
    arrays: list[np.ndarray] = []
    profile: dict[str, object] | None = None
    shape: tuple[int, int] | None = None
    crs = None
    transform = None

    for path in cog_paths:
        with rasterio.open(path) as src:
            ds: DatasetReader = src
            if profile is None:
                profile = dict(ds.profile)
                shape = (ds.height, ds.width)
                crs = ds.crs
                transform = ds.transform
            else:
                if (ds.height, ds.width) != shape:
                    raise ValueError(
                        f"shape mismatch in baseline stack: {path} is "
                        f"{(ds.height, ds.width)}, expected {shape}"
                    )
                if ds.crs != crs:
                    raise ValueError(f"CRS mismatch in baseline stack: {path}")
                if ds.transform != transform:
                    raise ValueError(f"transform mismatch in baseline stack: {path}")

            band = ds.read(1).astype(np.float32)
            nodata = ds.nodata
            if nodata is not None:
                band = np.where(band == nodata, np.nan, band)
            arrays.append(band)

    assert profile is not None  # for mypy; we set it on first iteration
    stack = np.stack(arrays, axis=0)
    return stack, profile


def _write_cog(array: np.ndarray, profile: dict[str, object], out_path: Path) -> None:
    """Write a single-band float32 array as a Cloud-Optimized GeoTIFF."""
    # Build a base GeoTIFF in a tmp file, then translate to COG using rio-cogeo
    # so the output passes ``rio cogeo validate``.  rio-cogeo's translate
    # needs a real on-disk source dataset.
    tmp_path = out_path.with_suffix(".raw.tif")
    write_profile = dict(profile)
    write_profile.update(
        {
            "count": 1,
            "dtype": "float32",
            "nodata": _NODATA,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }
    )
    # Remove driver-specific keys that may conflict with the output format.
    write_profile.pop("blockxsize", None)
    write_profile["blockxsize"] = 256
    write_profile.pop("blockysize", None)
    write_profile["blockysize"] = 256

    with rasterio.open(tmp_path, "w", **write_profile) as dst:
        dst.write(array, 1)

    dst_profile = cog_profiles.get("deflate")  # type: ignore[no-untyped-call]
    cog_translate(
        str(tmp_path),
        str(out_path),
        dst_profile,
        in_memory=False,
        quiet=True,
    )
    tmp_path.unlink(missing_ok=True)
