"""Synthetic SAR data fixtures for unit testing.

These helpers generate minimal fake SAFE directory structures and synthetic
rasters that exercise the processing pipeline without requiring real Sentinel-1
data or an active SNAP/GDAL installation in CI.

The fake SAFE layout only includes the files that the pipeline code touches:
- ``manifest.safe`` (empty placeholder)
- ``measurement/<polarization>.tif`` (small GeoTIFF)

For unit tests that need a real COG, ``make_synthetic_cog`` produces a 256x256
random raster wrapped as a Cloud-Optimized GeoTIFF using only ``rasterio`` and
``numpy`` (no SNAP required).
"""

from __future__ import annotations

import random
import string
from pathlib import Path

import numpy as np

# rasterio is a test dependency; we import at function scope so that the
# module can be imported even in environments without rasterio installed.


def make_fake_safe(
    tmp_dir: Path,
    *,
    mode: str = "GRD",
    polarization: str = "VV",
    rows: int = 64,
    cols: int = 64,
    seed: int = 42,
) -> Path:
    """Create a minimal fake Sentinel-1 SAFE directory for testing.

    The returned directory has enough structure to satisfy Path checks in
    ``pipeline.py``, but is NOT a valid SAFE product that SNAP can process.
    Its purpose is to allow unit tests to exercise pipeline logic up to the
    subprocess boundary (which is mocked).

    Parameters
    ----------
    tmp_dir:
        Parent directory under which the fake SAFE is created.
    mode:
        Product type string used in the SAFE name (``"GRD"`` or ``"SLC"``).
    polarization:
        Polarization label for the measurement band file.
    rows, cols:
        Dimensions of the fake measurement raster.
    seed:
        NumPy random seed for reproducibility.

    Returns
    -------
    Path
        Path to the created ``.SAFE`` directory.
    """
    rng = np.random.default_rng(seed)

    # Mimic the S1 SAFE naming convention (simplified)
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    safe_name = f"S1A_IW_{mode}_1SDV_20240101T000000_20240101T000030_{suffix}.SAFE"
    safe_dir = tmp_dir / safe_name
    measurement_dir = safe_dir / "measurement"
    measurement_dir.mkdir(parents=True)

    # Manifest placeholder
    (safe_dir / "manifest.safe").write_text(
        '<?xml version="1.0"?><xfdu:XFDU xmlns:xfdu="urn:ccsds:schema:xfdu:1"/>'
    )

    # Fake measurement raster (random uint16 values resembling raw SAR DN)
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    data = rng.integers(0, 65535, size=(rows, cols), dtype=np.uint16)
    transform = from_bounds(10.0, 50.0, 10.1, 50.1, cols, rows)
    crs = CRS.from_epsg(4326)

    band_path = measurement_dir / f"s1a-iw-{mode.lower()}-{polarization.lower()}-xxx.tif"
    with rasterio.open(
        band_path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=np.uint16,
        crs=crs,
        transform=transform,
    ) as ds:
        ds.write(data, 1)

    return safe_dir


def make_synthetic_cog(
    out_path: Path,
    *,
    rows: int = 256,
    cols: int = 256,
    seed: int = 0,
) -> Path:
    """Create a tiny synthetic Cloud-Optimized GeoTIFF for COG validation tests.

    The raster contains random float32 values in the range [-30, 5] dB
    (a plausible σ⁰ range for land surfaces) and is written with proper COG
    tiling and overviews using ``rio_cogeo``.

    This function requires ``rio_cogeo`` and ``rasterio`` to be installed.

    Parameters
    ----------
    out_path:
        Destination file path for the COG.
    rows, cols:
        Dimensions of the synthetic raster.
    seed:
        NumPy random seed for reproducibility.

    Returns
    -------
    Path
        ``out_path`` after successful creation.
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    rng = np.random.default_rng(seed)
    data = rng.uniform(-30.0, 5.0, size=(rows, cols)).astype(np.float32)
    transform = from_bounds(10.0, 50.0, 10.1, 50.1, cols, rows)
    crs = CRS.from_epsg(4326)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write intermediate plain GeoTIFF first
    tmp_path = out_path.with_suffix(".tmp.tif")
    with rasterio.open(
        tmp_path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=np.float32,
        crs=crs,
        transform=transform,
    ) as ds:
        ds.write(data, 1)

    # Convert to COG
    profile: dict[str, object] = dict(cog_profiles.get("deflate"))  # type: ignore[no-untyped-call]
    profile.update({"blockxsize": 64, "blockysize": 64})  # small tile for tiny test raster

    cog_translate(
        str(tmp_path),
        str(out_path),
        profile,
        overview_level=2,
        overview_resampling="nearest",
        quiet=True,
    )
    tmp_path.unlink(missing_ok=True)

    return out_path
