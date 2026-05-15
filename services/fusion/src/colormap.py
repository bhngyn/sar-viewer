"""Perceptually uniform colormaps for SAR fusion overlays.

Phase-2-brief.md §4.1 lists the allowed LUTs (``gray``, ``viridis``) and
explicitly bans ``jet`` and any rainbow LUT. Crameri et al. (2020)
"The misuse of colour in science communication" is the reference for the
ban: rainbow LUTs introduce false hue boundaries that humans read as data
boundaries, which is unacceptable for an investigation tool where the
investigator might mistake a colormap artefact for a real radar signature.

We sample matplotlib's colormap tables at instantiation rather than
calling ``matplotlib.pyplot`` at runtime, keeping the runtime stack lean
(no GUI backend imports).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from matplotlib import colormaps

Colormap = Literal["gray", "viridis"]

# Defensive: a runtime allow-list independent of the Literal so callers
# bypassing types (e.g. JSON dispatch from the worker) still get rejected.
_ALLOWED: frozenset[str] = frozenset({"gray", "viridis"})
_BANNED: frozenset[str] = frozenset({"jet", "rainbow", "hsv", "gist_rainbow", "nipy_spectral"})


def apply_colormap(intensity_01: np.ndarray, name: Colormap | str) -> np.ndarray:
    """Apply a perceptually uniform colormap to a [0, 1] intensity array.

    Parameters
    ----------
    intensity_01:
        Float array with values in [0, 1]. Values outside that range are
        clipped. Shape ``(H, W)``.
    name:
        Colormap name. Must be in ``_ALLOWED``; ``jet`` and similar rainbow
        LUTs raise ``ValueError``.

    Returns
    -------
    np.ndarray
        ``(H, W, 3)`` uint8 RGB.
    """
    if name in _BANNED:
        raise ValueError(
            f"{name!r} is banned for fusion (rainbow LUTs introduce false "
            "hue boundaries; see docs/architecture.md and Crameri 2020). "
            f"Use one of: {sorted(_ALLOWED)}."
        )
    if name not in _ALLOWED:
        raise ValueError(f"unsupported colormap {name!r}; pick one of: {sorted(_ALLOWED)}")

    cmap = colormaps.get_cmap(name)
    clipped = np.clip(intensity_01, 0.0, 1.0)
    rgba = cmap(clipped)  # (H, W, 4) float in [0,1]
    rgb: np.ndarray = (rgba[..., :3] * 255.0).astype(np.uint8)
    return rgb


def db_to_normalized(db: np.ndarray, low_db: float = -25.0, high_db: float = 0.0) -> np.ndarray:
    """Scale σ⁰ dB values to [0, 1] for colormapping.

    The default range (-25, 0) covers most Sentinel-1 land scenes; out-of-
    range values are clipped. NaN inputs become 0 (rendered as the
    colormap minimum).
    """
    finite = np.where(np.isfinite(db), db, low_db)
    return np.clip((finite - low_db) / (high_db - low_db), 0.0, 1.0).astype(np.float32)
