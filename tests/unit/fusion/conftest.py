"""Shared synthetic-raster helpers for fusion tests.

All fixtures are pure numpy + rasterio — no network, no real SAR data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def write_rgb_cog(
    path: Path,
    rgb: np.ndarray,
    *,
    pixel_size_m: float = 10.0,
    origin: tuple[float, float] = (0.0, 1000.0),
    crs: str = "EPSG:32633",
) -> Path:
    """Write an (H, W, 3) uint8 array as a 3-band GeoTIFF.

    The fusion code reads via rasterio so writing a plain GeoTIFF (not a
    fully-tiled COG) is enough for tests; rio cogeo validate is exercised
    in the production write path via ``_write_rgb_cog``.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3), got {rgb.shape}")
    h, w, _ = rgb.shape
    transform = from_origin(origin[0], origin[1], pixel_size_m, pixel_size_m)
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 3,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": 64,
        "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        for i in range(3):
            dst.write(rgb[:, :, i], i + 1)
    return path


def write_db_cog(
    path: Path,
    db: np.ndarray,
    *,
    pixel_size_m: float = 10.0,
    origin: tuple[float, float] = (0.0, 1000.0),
    crs: str = "EPSG:32633",
) -> Path:
    h, w = db.shape
    transform = from_origin(origin[0], origin[1], pixel_size_m, pixel_size_m)
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": float("nan"),
        "tiled": True,
        "blockxsize": 64,
        "blockysize": 64,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(db.astype(np.float32), 1)
    return path
