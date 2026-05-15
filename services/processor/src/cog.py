"""Cloud-Optimized GeoTIFF (COG) conversion and validation.

We use ``rio-cogeo`` for conversion and validation because it correctly handles
the GDAL overview/tiling interleaving required for a valid COG.  A naive
``gdal_translate -co TILED=YES`` produces a file that *looks* like a COG but
fails the strict COG validator because the overviews are written after the main
IFDs.  ``rio-cogeo`` always writes overviews first.

Reference: https://www.cogeo.org/
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def convert_to_cog(src: Path, dst: Path) -> Path:
    """Convert ``src`` to a Cloud-Optimized GeoTIFF at ``dst``.

    Uses ``rio_cogeo.cogeo.cog_translate`` with sensible defaults:
    - Deflate compression (lossless, good ratio for SAR dB imagery)
    - 512x512 tile size (good balance for HTTP range requests)
    - GDAL overviews (2, 4, 8, 16, 32, 64)
    - NEAREST resampling for overviews (preserves backscatter statistics)

    Parameters
    ----------
    src:
        Input raster path (e.g. SNAP GeoTIFF output).
    dst:
        Destination COG path.

    Returns
    -------
    Path
        ``dst`` after successful conversion.

    Raises
    ------
    FileNotFoundError
        If ``src`` does not exist.
    RuntimeError
        If ``rio_cogeo`` validation fails after conversion.
    """
    if not src.exists():
        raise FileNotFoundError(f"Source raster not found: {src}")

    log = logger.bind(src=str(src), dst=str(dst))
    log.info("cog_convert_start")

    # Import here so the module can be imported even if rio-cogeo is not installed
    # in the test environment (unit tests mock this function at the boundary).
    from rio_cogeo.cogeo import cog_translate
    from rio_cogeo.profiles import cog_profiles

    dst.parent.mkdir(parents=True, exist_ok=True)

    profile: dict[str, object] = dict(cog_profiles.get("deflate"))  # type: ignore[no-untyped-call]
    profile.update(
        {
            "blockxsize": 512,
            "blockysize": 512,
        }
    )

    cog_translate(
        str(src),
        str(dst),
        profile,
        overview_level=6,
        overview_resampling="nearest",
        quiet=True,
    )

    _validate_cog(dst)
    log.info("cog_convert_done")
    return dst


def _validate_cog(path: Path) -> None:
    """Raise ``RuntimeError`` if ``path`` is not a valid COG.

    We use the strict ``rio_cogeo`` validator rather than just checking the file
    exists, because a non-COG GeoTIFF would silently degrade TiTiler performance
    (it would read the whole file for every tile instead of using HTTP range
    requests).
    """
    from rio_cogeo.cogeo import cog_validate

    is_valid, errors, warnings = cog_validate(str(path))
    if not is_valid:
        raise RuntimeError(
            f"COG validation failed for {path}: errors={errors}, warnings={warnings}"
        )
    if warnings:
        logger.warning("cog_validate_warnings", path=str(path), warnings=warnings)


def validate_cog(path: Path) -> bool:
    """Return True if ``path`` is a valid COG, False otherwise.

    Public helper for use in tests and health checks.
    """
    try:
        from rio_cogeo.cogeo import cog_validate

        is_valid, _, _ = cog_validate(str(path))
        return is_valid
    except Exception:
        return False
