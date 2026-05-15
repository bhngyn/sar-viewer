"""SAR-on-S2 fusion — combine the Sentinel-2 basemap with SAR products.

Four modes (phase-2-brief.md §4.1):

1. **side_by_side** — no fusion; returns ``sar_cog`` unchanged. The UI
   handles two viewports. Exposed for API uniformity.

2. **sar_on_s2** — SAR (σ⁰ dB) mapped through a perceptually uniform
   colormap (``gray`` default, ``viridis`` allowed; ``jet`` banned),
   alpha-blended onto the S2 RGB basemap. Default ``alpha=0.5``.

3. **anomaly_highlight** — S2 RGB basemap with anomaly bboxes outlined and
   filled at 25% opacity. Per the brief:
   - ``new`` clusters → red (#d62728)
   - ``missing`` clusters → cyan (#17becf)
   - ``intensity_change`` clusters → yellow (#bcbd22) — chosen as a
     neutral, non-alarming tone between the new/missing extremes (these
     anomalies are real-but-ambiguous, and a hot colour would over-signal
     to the investigator).

4. **change_rgb_on_s2** — the multitemporal RGB composite alpha-blended
   over S2 with a hard 50% alpha cap. Values above 0.5 raise ``ValueError``.

All fused outputs are 3-band uint8 Cloud-Optimized GeoTIFFs with a
"Sentinel-1 ~10m" watermark in the bottom-right (toggle via ``watermark``
kwarg or the ``WATERMARK`` env var). The S2 CRS and transform are
preserved.

References
----------
- Crameri et al. (2020) "The misuse of colour in science communication" —
  why ``jet`` is banned and why we colour anomalies with categorical hues
  rather than diverging colormaps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
import rasterio
import structlog
from rasterio.transform import rowcol
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles

from services.fusion.src.colormap import Colormap, apply_colormap, db_to_normalized
from services.fusion.src.watermark import stamp_watermark, watermark_enabled
from shared.models import Anomaly

log: structlog.BoundLogger = structlog.get_logger(__name__)

Mode = Literal["side_by_side", "sar_on_s2", "anomaly_highlight", "change_rgb_on_s2"]

_MAX_CHANGE_RGB_ALPHA: float = 0.5

# Anomaly fill colours (RGB tuples) — see module docstring.
_COLOR_NEW: tuple[int, int, int] = (214, 39, 40)  # #d62728
_COLOR_MISSING: tuple[int, int, int] = (23, 190, 207)  # #17becf
_COLOR_INTENSITY: tuple[int, int, int] = (188, 189, 34)  # #bcbd22


def fuse(
    mode: Mode,
    s2_cog: Path,
    sar_cog: Path | None = None,
    rgb_cog: Path | None = None,
    anomalies: list[Anomaly] | None = None,
    out_path: Path = Path("fused.tif"),
    *,
    alpha: float = 0.5,
    colormap: Colormap | str = "gray",
    watermark: bool = True,
) -> Path:
    """Produce a fused COG and return its path.

    Per-mode required args:
      - ``side_by_side``: ``sar_cog``. Returns the SAR path unchanged.
      - ``sar_on_s2``: ``sar_cog``.
      - ``anomaly_highlight``: ``anomalies`` (may be an empty list).
      - ``change_rgb_on_s2``: ``rgb_cog``; ``alpha`` strictly ≤ 0.5.

    Always rejects ``colormap="jet"`` defensively (in case a caller bypasses
    the ``Literal`` type via JSON dispatch).
    """
    if colormap == "jet":
        # Defensive — even if the Literal is bypassed via untyped JSON.
        from services.fusion.src.colormap import apply_colormap as _ac

        _ac(np.zeros((1, 1), dtype=np.float32), "jet")  # raises ValueError

    if mode == "side_by_side":
        if sar_cog is None:
            raise ValueError("side_by_side requires sar_cog")
        log.info("fuse.side_by_side", sar=str(sar_cog))
        return sar_cog

    if mode == "change_rgb_on_s2":
        if alpha > _MAX_CHANGE_RGB_ALPHA:
            raise ValueError(
                f"change_rgb_on_s2 alpha must be ≤ {_MAX_CHANGE_RGB_ALPHA} "
                f"(basemap context must remain visible); got {alpha}"
            )
        if rgb_cog is None:
            raise ValueError("change_rgb_on_s2 requires rgb_cog")

    if mode == "sar_on_s2" and sar_cog is None:
        raise ValueError("sar_on_s2 requires sar_cog")

    if mode == "anomaly_highlight" and anomalies is None:
        raise ValueError("anomaly_highlight requires anomalies (may be empty list)")

    s2_rgb, s2_profile = _read_rgb(s2_cog)

    if mode == "sar_on_s2":
        assert sar_cog is not None
        out_rgb = _fuse_sar_on_s2(s2_rgb, sar_cog, alpha=alpha, colormap=colormap)
    elif mode == "anomaly_highlight":
        out_rgb = _fuse_anomaly_highlight(
            s2_rgb,
            s2_profile,
            anomalies if anomalies is not None else [],
        )
    elif mode == "change_rgb_on_s2":
        assert rgb_cog is not None
        out_rgb = _fuse_change_rgb_on_s2(s2_rgb, rgb_cog, alpha=alpha)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if watermark_enabled(watermark):
        out_rgb = stamp_watermark(out_rgb)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_rgb_cog(out_rgb, s2_profile, out_path)
    log.info("fuse.write", mode=mode, path=str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# Per-mode implementations
# ---------------------------------------------------------------------------


def _fuse_sar_on_s2(
    s2_rgb: np.ndarray,
    sar_cog: Path,
    *,
    alpha: float,
    colormap: Colormap | str,
) -> np.ndarray:
    sar_db, _ = _read_band(sar_cog)
    if sar_db.shape != s2_rgb.shape[:2]:
        raise ValueError(f"SAR shape {sar_db.shape} does not match S2 shape {s2_rgb.shape[:2]}")
    sar_norm = db_to_normalized(sar_db)
    sar_rgb = apply_colormap(sar_norm, colormap)
    # Alpha blend: out = (1-alpha) * s2 + alpha * sar
    return _blend(s2_rgb, sar_rgb, alpha)


def _fuse_change_rgb_on_s2(
    s2_rgb: np.ndarray,
    rgb_cog: Path,
    *,
    alpha: float,
) -> np.ndarray:
    rgb, _ = _read_rgb(rgb_cog)
    if rgb.shape != s2_rgb.shape:
        raise ValueError(f"change-RGB shape {rgb.shape} does not match S2 shape {s2_rgb.shape}")
    return _blend(s2_rgb, rgb, alpha)


def _fuse_anomaly_highlight(
    s2_rgb: np.ndarray,
    s2_profile: dict[str, object],
    anomalies: list[Anomaly],
) -> np.ndarray:
    """Draw filled/outlined anomaly bboxes onto the S2 basemap."""
    out = s2_rgb.copy()
    transform = s2_profile["transform"]
    h, w = out.shape[:2]
    for anomaly in anomalies:
        color = _color_for_kind(anomaly.kind)
        minx, miny, maxx, maxy = anomaly.bbox
        # rowcol(x, y) returns (row, col); top-left is (maxy, minx),
        # bottom-right is (miny, maxx).  Clamp to raster bounds.
        try:
            r0, c0 = rowcol(transform, minx, maxy)  # top-left
            r1, c1 = rowcol(transform, maxx, miny)  # bottom-right
        except Exception:
            # If the bbox is outside the raster CRS extent rowcol may
            # throw — skip rather than break the whole fusion.
            continue
        r0, r1 = sorted([int(r0), int(r1)])
        c0, c1 = sorted([int(c0), int(c1)])
        r0 = max(0, min(h - 1, r0))
        r1 = max(0, min(h - 1, r1))
        c0 = max(0, min(w - 1, c0))
        c1 = max(0, min(w - 1, c1))
        if r0 == r1 or c0 == c1:
            continue
        # 25% fill: blend a 75/25 mix of basemap and color over the bbox.
        patch_color = np.array(color, dtype=np.float32)[None, None, :]
        region = out[r0 : r1 + 1, c0 : c1 + 1].astype(np.float32)
        out[r0 : r1 + 1, c0 : c1 + 1] = (0.75 * region + 0.25 * patch_color).astype(np.uint8)
        # 100% outline: paint top/bottom rows and left/right cols.
        out[r0, c0 : c1 + 1] = color
        out[r1, c0 : c1 + 1] = color
        out[r0 : r1 + 1, c0] = color
        out[r0 : r1 + 1, c1] = color
    return out


def _color_for_kind(kind: str) -> tuple[int, int, int]:
    if kind == "new":
        return _COLOR_NEW
    if kind == "missing":
        return _COLOR_MISSING
    return _COLOR_INTENSITY


def _blend(base: np.ndarray, top: np.ndarray, alpha: float) -> np.ndarray:
    """Alpha-blend ``top`` onto ``base`` (both uint8 RGB)."""
    if base.shape != top.shape:
        raise ValueError(f"blend shape mismatch: {base.shape} vs {top.shape}")
    if alpha == 0.0:
        return base.copy()
    a = float(alpha)
    return ((1.0 - a) * base.astype(np.float32) + a * top.astype(np.float32)).astype(np.uint8)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _read_rgb(path: Path) -> tuple[np.ndarray, dict[str, object]]:
    """Read a 3-band uint8 RGB COG → ``(H, W, 3)`` uint8 array + profile."""
    with rasterio.open(path) as src:
        if src.count < 3:
            raise ValueError(f"{path} has {src.count} bands; an RGB basemap needs ≥3")
        bands = np.stack([src.read(i) for i in (1, 2, 3)], axis=0)
        profile: dict[str, object] = dict(src.profile)
        profile["transform"] = src.transform
        profile["crs"] = src.crs
    if bands.dtype != np.uint8:
        # Stretch to uint8 if the input is a float — pragmatic helper for
        # synthetic test inputs.
        if np.issubdtype(bands.dtype, np.floating):
            bands = np.clip(bands, 0, 255).astype(np.uint8)
        else:
            bands = bands.astype(np.uint8)
    return np.transpose(bands, (1, 2, 0)), profile


def _read_band(path: Path) -> tuple[np.ndarray, dict[str, object]]:
    with rasterio.open(path) as src:
        band = src.read(1).astype(np.float32)
        if src.nodata is not None:
            band = np.where(band == src.nodata, np.nan, band)
        profile: dict[str, object] = dict(src.profile)
        profile["transform"] = src.transform
        profile["crs"] = src.crs
    return band, profile


def _write_rgb_cog(
    rgb_hwc: np.ndarray,
    profile: dict[str, object],
    out_path: Path,
) -> None:
    """Write ``(H, W, 3)`` uint8 → 3-band Cloud-Optimized GeoTIFF."""
    if rgb_hwc.ndim != 3 or rgb_hwc.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3), got {rgb_hwc.shape}")
    rgb_chw = np.transpose(rgb_hwc, (2, 0, 1)).astype(np.uint8)

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
        for i in range(3):
            dst.write(rgb_chw[i], i + 1)

    dst_profile = cog_profiles.get("deflate")  # type: ignore[no-untyped-call]
    cog_translate(
        str(tmp_path),
        str(out_path),
        dst_profile,
        in_memory=False,
        quiet=True,
    )
    tmp_path.unlink(missing_ok=True)
