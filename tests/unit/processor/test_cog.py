"""Unit tests for COG creation and validation.

The key test here produces a real Cloud-Optimized GeoTIFF (256x256 synthetic
raster) and validates it with ``rio_cogeo.cogeo.cog_validate``.  This test
requires ``rasterio`` and ``rio-cogeo`` to be installed but does NOT require
SNAP.

All other tests mock the rio_cogeo boundary and test error-handling logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.processor.src.cog import convert_to_cog, validate_cog
from services.processor.src.fixtures import make_synthetic_cog

# ---------------------------------------------------------------------------
# Synthetic COG creation + validation (real rio-cogeo call)
# ---------------------------------------------------------------------------


class TestMakeSyntheticCog:
    """Integration-style test — uses real rasterio/rio-cogeo, no SNAP."""

    def test_synthetic_cog_passes_validation(self, tmp_path: Path) -> None:
        """A COG produced by make_synthetic_cog must pass cog_validate."""
        cog_path = tmp_path / "test_sigma0.tif"
        result = make_synthetic_cog(cog_path, rows=256, cols=256)
        assert result == cog_path
        assert cog_path.exists()
        assert validate_cog(cog_path), f"COG validation failed for {cog_path}"

    def test_synthetic_cog_is_created_at_specified_path(self, tmp_path: Path) -> None:
        cog_path = tmp_path / "subdir" / "sigma0.tif"
        make_synthetic_cog(cog_path)
        assert cog_path.exists()

    def test_synthetic_cog_different_seeds_produce_different_files(self, tmp_path: Path) -> None:
        path1 = tmp_path / "cog1.tif"
        path2 = tmp_path / "cog2.tif"
        make_synthetic_cog(path1, seed=0)
        make_synthetic_cog(path2, seed=99)
        assert path1.read_bytes() != path2.read_bytes()


# ---------------------------------------------------------------------------
# convert_to_cog error-handling tests (mocked boundary)
# ---------------------------------------------------------------------------


class TestConvertToCog:
    def test_raises_if_src_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Source raster not found"):
            convert_to_cog(tmp_path / "nonexistent.tif", tmp_path / "out.tif")

    def test_calls_cog_translate(self, tmp_path: Path) -> None:
        src = tmp_path / "src.tif"
        src.write_bytes(b"fake")
        dst = tmp_path / "dst.tif"

        # cog_translate and cog_profiles are imported inside convert_to_cog,
        # so we patch them at their source module, not on cog.py.
        with (
            patch("rio_cogeo.cogeo.cog_translate") as mock_translate,
            patch("rio_cogeo.profiles.cog_profiles") as mock_profiles,
            patch("services.processor.src.cog._validate_cog") as mock_validate,
        ):
            mock_profiles.get.return_value = {
                "compress": "deflate",
                "blockxsize": 512,
                "blockysize": 512,
            }
            mock_translate.return_value = None
            mock_validate.return_value = None

            result = convert_to_cog(src, dst)

        mock_translate.assert_called_once()
        assert result == dst

    def test_validation_failure_raises_runtime_error(self, tmp_path: Path) -> None:
        src = tmp_path / "src.tif"
        src.write_bytes(b"fake")
        dst = tmp_path / "dst.tif"

        # Patch at source module for functions imported inside cog.py functions.
        with (
            patch("rio_cogeo.cogeo.cog_translate"),
            patch("rio_cogeo.profiles.cog_profiles") as mock_profiles,
            patch("rio_cogeo.cogeo.cog_validate") as mock_validate,
        ):
            mock_profiles.get.return_value = {
                "compress": "deflate",
                "blockxsize": 512,
                "blockysize": 512,
            }
            # Simulate COG validation failure
            mock_validate.return_value = (False, ["missing overview"], [])

            with pytest.raises(RuntimeError, match="COG validation failed"):
                convert_to_cog(src, dst)


# ---------------------------------------------------------------------------
# validate_cog helper tests
# ---------------------------------------------------------------------------


class TestValidateCog:
    def test_returns_true_for_valid_cog(self, tmp_path: Path) -> None:
        cog_path = tmp_path / "valid.tif"
        make_synthetic_cog(cog_path, rows=64, cols=64)
        assert validate_cog(cog_path) is True

    def test_returns_false_for_nonexistent_path(self, tmp_path: Path) -> None:
        assert validate_cog(tmp_path / "ghost.tif") is False

    def test_returns_false_for_non_tiff(self, tmp_path: Path) -> None:
        garbage = tmp_path / "not_a_tif.tif"
        garbage.write_bytes(b"this is not a TIFF")
        assert validate_cog(garbage) is False
