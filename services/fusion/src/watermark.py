"""Resolution-caveat watermark renderer.

Every fused COG produced by the tool must carry a visible reminder of
Sentinel-1's resolution limit so the investigator does not mistake a
single radar pixel for a sub-metre feature. See CLAUDE.md §0 ("Honest
capability ceiling") and phase-2-brief.md §4.1.

The watermark is rendered via Pillow into a small RGB patch and composited
onto the bottom-right corner of the raster before encoding. Pillow is a
build-time dependency of the fusion service.

Off-switch: ``WATERMARK=0`` (or ``false``) in the env, OR ``watermark=False``
to :func:`services.fusion.src.fuse.fuse`. The env switch is the operator
override; the kwarg is the per-call override.
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_TEXT: str = "Sentinel-1 ~10m"


def watermark_enabled(call_arg: bool) -> bool:
    """Return True iff watermarking should run for this call.

    ``call_arg=False`` always disables. Otherwise the ``WATERMARK`` env
    var can disable globally (``0``, ``false``, ``no``); any other value
    (including unset) keeps the watermark on.
    """
    if not call_arg:
        return False
    env = os.environ.get("WATERMARK", "").lower()
    return env not in ("0", "false", "no", "off")


def stamp_watermark(
    rgb: np.ndarray,
    text: str = DEFAULT_TEXT,
    *,
    padding_px: int = 6,
) -> np.ndarray:
    """Composite a text watermark onto the bottom-right of ``rgb``.

    Parameters
    ----------
    rgb:
        ``(H, W, 3)`` uint8 RGB array. Modified out-of-place; a new array
        is returned (the input is not mutated).
    text:
        Text to render. Default "Sentinel-1 ~10m".
    padding_px:
        Pixels of margin between the text patch and the image edge.

    Returns
    -------
    np.ndarray
        New ``(H, W, 3)`` uint8 array with the watermark composited in.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) RGB, got shape {rgb.shape}")
    if rgb.dtype != np.uint8:
        raise ValueError(f"expected uint8, got {rgb.dtype}")

    h, w, _ = rgb.shape
    # Choose a font size proportional to the image so the watermark is
    # visible on small thumbnails and not gigantic on full-resolution COGs.
    font_size = max(8, min(20, h // 32))
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        # Default bitmap font is always available in Pillow; we fall back
        # so the watermark works even on stripped-down images.
        font = ImageFont.load_default()

    # Measure the rendered text on a throwaway draw to size our patch.
    measure_img = Image.new("RGB", (1, 1))
    bbox = ImageDraw.Draw(measure_img).textbbox((0, 0), text, font=font)
    text_w = int(bbox[2] - bbox[0])
    text_h = int(bbox[3] - bbox[1])
    patch_w = int(text_w + 2 * padding_px)
    patch_h = int(text_h + 2 * padding_px)

    # If the raster is smaller than the watermark patch, just return the
    # original unmodified — better no stamp than an unreadable smear.
    if patch_w >= w or patch_h >= h:
        return rgb.copy()

    # Render the patch (dark semi-transparent background + light text).
    patch = Image.new("RGB", (patch_w, patch_h), (0, 0, 0))
    draw = ImageDraw.Draw(patch)
    draw.text(
        (padding_px, padding_px - int(bbox[1])),
        text,
        fill=(255, 255, 255),
        font=font,
    )
    patch_arr = np.asarray(patch, dtype=np.uint8)

    out = rgb.copy()
    y0 = h - patch_h - padding_px
    x0 = w - patch_w - padding_px
    # Simple opaque overlay (semi-transparency would require an alpha
    # blend; pure black box with white text on top of any basemap remains
    # readable).
    out[y0 : y0 + patch_h, x0 : x0 + patch_w, :] = patch_arr
    return out
