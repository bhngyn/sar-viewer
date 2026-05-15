"""Shared synthetic-raster helpers for analyzer tests.

All fixtures are pure numpy — no network, no real SAR data, all generated
deterministically from ``numpy.random.default_rng(seed)`` so the tests are
reproducible and tiny (<100 kB) per phase-2-brief.md §1.4.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def write_synthetic_db_cog(
    path: Path,
    array: np.ndarray,
    *,
    pixel_size_m: float = 10.0,
    origin: tuple[float, float] = (0.0, 1000.0),
    crs: str = "EPSG:32633",  # UTM zone 33N — any projected CRS works
    nodata: float = float("nan"),
) -> Path:
    """Write a single-band float32 GeoTIFF of σ⁰ dB values.

    We write a plain GeoTIFF (not a COG) — the analyzer reads bands either
    way, and ``rio cogeo validate`` is exercised separately on the analyzer's
    output. Keeping the helper simple keeps tests fast.
    """
    h, w = array.shape
    transform = from_origin(origin[0], origin[1], pixel_size_m, pixel_size_m)
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 64,
        "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)
    return path


def synthetic_db_stack(
    n_scenes: int,
    shape: tuple[int, int] = (64, 64),
    *,
    mean_db: float = -12.0,
    std_db: float = 2.0,
    seed: int = 0,
) -> np.ndarray:
    """Return a (n_scenes, H, W) stack of Gaussian σ⁰ dB values."""
    rng = np.random.default_rng(seed)
    return rng.normal(loc=mean_db, scale=std_db, size=(n_scenes, *shape)).astype(np.float32)


def gamma_intensity_series(
    k: int,
    *,
    enl: float = 4.4,
    mean_intensity: float = 0.1,
    seed: int = 0,
) -> np.ndarray:
    """Return a length-k array of Gamma-distributed intensities.

    ``scipy.stats.gamma`` uses (shape, scale). With shape=enl and
    scale=mean/enl, the resulting samples have mean=mean_intensity and
    coefficient of variation 1/sqrt(enl) — the standard SAR speckle model.
    """
    rng = np.random.default_rng(seed)
    return rng.gamma(shape=enl, scale=mean_intensity / enl, size=k)
