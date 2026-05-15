"""Multitemporal RGB composite for Sentinel-1 σ⁰ stacks.

Two modes (phase-2-brief.md §3):

- ``physics`` (default, Cian et al., IEEE doi 10.1109/JSTARS.2019.2904035) —
  R = most recent VV (dB), G = median of prior VV, B = stddev across the
  stack. Designed for "what is bright now that wasn't before" scanning;
  requires ≥3 scenes.

- ``change`` — pure pre/post visualization for change detection.
  R = pre VV, G = post VV, B = (pre + post) / 2. Exactly 2 scenes; pre/post
  is determined by ``dates`` (earliest = pre).

Each channel is stretched per-band to ``[0, 255]`` uint8 via a configurable
percentile range (default 2-98%) and written as a 3-band Cloud-Optimized
GeoTIFF that passes ``rio cogeo validate``.

Inputs are assumed already coregistered (the processor pipeline produces
terrain-corrected COGs on a common grid). If shapes disagree the function
raises ``ValueError``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
import rasterio
import structlog
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles

log: structlog.BoundLogger = structlog.get_logger(__name__)

Mode = Literal["physics", "change"]


def build_multitemporal_rgb(
    cog_paths: list[Path],
    dates: list[datetime],
    out_path: Path,
    *,
    mode: Mode = "physics",
    stretch_pct: tuple[float, float] = (2.0, 98.0),
) -> Path:
    """Build a 3-band uint8 RGB composite COG and return its path.

    Parameters
    ----------
    cog_paths:
        Paths to single-band σ⁰ (dB) COGs from the same AOI.
    dates:
        Acquisition datetimes parallel to ``cog_paths``. Used to determine
        ordering for ``change`` mode and to identify "most recent" for
        ``physics`` mode.
    out_path:
        Path to write the output 3-band COG. Parent directories are
        created if needed.
    mode:
        ``"physics"`` (default) or ``"change"``.
    stretch_pct:
        ``(low, high)`` percentiles used to stretch each band to [0, 255].
        Default ``(2.0, 98.0)`` keeps outliers from compressing the bulk of
        the dynamic range.

    Returns
    -------
    Path
        ``out_path``, after writing.

    Raises
    ------
    ValueError
        On shape mismatch, length mismatch between ``cog_paths`` and
        ``dates``, fewer than 3 inputs for ``physics``, or != 2 inputs for
        ``change``.
    """
    if len(cog_paths) != len(dates):
        raise ValueError(
            f"cog_paths and dates must have the same length (got {len(cog_paths)} vs {len(dates)})"
        )
    if mode == "physics" and len(cog_paths) < 3:
        raise ValueError(f"'physics' mode requires at least 3 input scenes, got {len(cog_paths)}")
    if mode == "change" and len(cog_paths) != 2:
        raise ValueError(f"'change' mode requires exactly 2 input scenes, got {len(cog_paths)}")
    if stretch_pct[0] >= stretch_pct[1]:
        raise ValueError(f"stretch_pct must satisfy low < high, got {stretch_pct}")
    if stretch_pct[0] < 0 or stretch_pct[1] > 100:
        raise ValueError(f"stretch_pct must be in [0, 100], got {stretch_pct}")

    stack, profile = _read_stack(cog_paths)

    if mode == "physics":
        red_db, green_db, blue_db = _physics_channels(stack, dates)
    else:
        red_db, green_db, blue_db = _change_channels(stack, dates)

    red = _stretch_to_uint8(red_db, stretch_pct)
    green = _stretch_to_uint8(green_db, stretch_pct)
    blue = _stretch_to_uint8(blue_db, stretch_pct)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_rgb_cog(np.stack([red, green, blue], axis=0), profile, out_path)
    log.info("rgb.write", path=str(out_path), mode=mode)
    return out_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _physics_channels(
    stack: np.ndarray,
    dates: list[datetime],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """R = latest VV, G = median of priors, B = stddev across the stack."""
    latest_idx = int(np.argmax([d.timestamp() for d in dates]))
    latest = stack[latest_idx]
    prior_mask = np.ones(stack.shape[0], dtype=bool)
    prior_mask[latest_idx] = False
    prior = stack[prior_mask]
    with np.errstate(invalid="ignore"):
        green = np.nanmedian(prior, axis=0)
        blue = np.nanstd(stack, axis=0)
    return latest.astype(np.float32), green.astype(np.float32), blue.astype(np.float32)


def _change_channels(
    stack: np.ndarray,
    dates: list[datetime],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """R = pre, G = post, B = mean(pre, post)."""
    # Earliest date is pre.
    if dates[0] <= dates[1]:
        pre, post = stack[0], stack[1]
    else:
        pre, post = stack[1], stack[0]
    avg = ((pre + post) / 2.0).astype(np.float32)
    return pre.astype(np.float32), post.astype(np.float32), avg


def _stretch_to_uint8(
    arr: np.ndarray,
    stretch_pct: tuple[float, float],
) -> np.ndarray:
    """Stretch a float array to uint8 [0,255] using percentile clamps.

    NaN pixels become 0 in the output.  The stretch is computed over
    finite values only so outliers / NoData don't compress the dynamic
    range to a sliver of the byte axis.
    """
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros(arr.shape, dtype=np.uint8)
    lo, hi = np.percentile(arr[finite], stretch_pct)
    if hi == lo:
        # Flat input — return a uniform mid-grey rather than dividing by zero.
        return np.full(arr.shape, 128, dtype=np.uint8)
    clipped = np.clip(arr, lo, hi)
    scaled = (clipped - lo) / (hi - lo) * 255.0
    scaled = np.where(np.isfinite(arr), scaled, 0.0)
    return scaled.astype(np.uint8)


def _read_stack(cog_paths: list[Path]) -> tuple[np.ndarray, dict[str, object]]:
    """Read a list of single-band COGs into an (N, H, W) float32 stack."""
    arrays: list[np.ndarray] = []
    profile: dict[str, object] | None = None
    shape: tuple[int, int] | None = None

    for path in cog_paths:
        with rasterio.open(path) as src:
            if profile is None:
                profile = dict(src.profile)
                shape = (src.height, src.width)
            elif (src.height, src.width) != shape:
                raise ValueError(
                    f"shape mismatch: {path} is {(src.height, src.width)}, expected {shape}"
                )
            band = src.read(1).astype(np.float32)
            if src.nodata is not None:
                band = np.where(band == src.nodata, np.nan, band)
            arrays.append(band)

    assert profile is not None
    stack = np.stack(arrays, axis=0)
    return stack, profile


def _write_rgb_cog(
    rgb: np.ndarray,
    profile: dict[str, object],
    out_path: Path,
) -> None:
    """Write a (3, H, W) uint8 array as a Cloud-Optimized GeoTIFF."""
    assert rgb.shape[0] == 3, rgb.shape
    write_profile = dict(profile)
    write_profile.update(
        {
            "count": 3,
            "dtype": "uint8",
            "nodata": None,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }
    )

    tmp_path = out_path.with_suffix(".raw.tif")
    with rasterio.open(tmp_path, "w", **write_profile) as dst:
        for band_idx in range(3):
            dst.write(rgb[band_idx], band_idx + 1)

    dst_profile = cog_profiles.get("deflate")  # type: ignore[no-untyped-call]
    cog_translate(
        str(tmp_path),
        str(out_path),
        dst_profile,
        in_memory=False,
        quiet=True,
    )
    tmp_path.unlink(missing_ok=True)
