"""sar_on_s2 mode: SAR colour-mapped over S2 basemap."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import rasterio

from services.fusion.src.fuse import fuse

from .conftest import write_db_cog, write_rgb_cog


def _make_s2_and_sar(tmp_path: Path, shape: tuple[int, int] = (64, 64)):
    rng = np.random.default_rng(0)
    s2 = rng.integers(0, 255, size=(*shape, 3), dtype=np.uint8)
    sar_db = rng.normal(loc=-12.0, scale=2.0, size=shape).astype(np.float32)
    s2_path = write_rgb_cog(tmp_path / "s2.tif", s2)
    sar_path = write_db_cog(tmp_path / "sar.tif", sar_db)
    return s2, s2_path, sar_db, sar_path


class TestFuseSarOnS2:
    def test_alpha_zero_returns_basemap_modulo_watermark(self, tmp_path: Path) -> None:
        """alpha=0 → fused output equals S2 everywhere except the watermark."""
        s2, s2_path, _, sar_path = _make_s2_and_sar(tmp_path)
        out = tmp_path / "fused.tif"
        fuse(
            "sar_on_s2",
            s2_cog=s2_path,
            sar_cog=sar_path,
            out_path=out,
            alpha=0.0,
            watermark=False,  # disable so we can compare pixel-for-pixel
        )
        with rasterio.open(out) as src:
            out_arr = np.stack([src.read(i) for i in (1, 2, 3)], axis=-1)
        # Compression may shift bytes very slightly; allow exact equality.
        assert np.array_equal(out_arr, s2)

    def test_output_is_three_band_uint8(self, tmp_path: Path) -> None:
        _, s2_path, _, sar_path = _make_s2_and_sar(tmp_path)
        out = tmp_path / "fused.tif"
        fuse("sar_on_s2", s2_cog=s2_path, sar_cog=sar_path, out_path=out, alpha=0.5)
        with rasterio.open(out) as src:
            assert src.count == 3
            assert src.dtypes == ("uint8", "uint8", "uint8")

    def test_output_passes_rio_cogeo_validate(self, tmp_path: Path) -> None:
        _, s2_path, _, sar_path = _make_s2_and_sar(tmp_path)
        out = tmp_path / "fused.tif"
        fuse("sar_on_s2", s2_cog=s2_path, sar_cog=sar_path, out_path=out, alpha=0.5)
        proc = subprocess.run(
            ["uv", "run", "rio", "cogeo", "validate", str(out)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    def test_viridis_works(self, tmp_path: Path) -> None:
        _, s2_path, _, sar_path = _make_s2_and_sar(tmp_path)
        out = tmp_path / "fused.tif"
        fuse(
            "sar_on_s2",
            s2_cog=s2_path,
            sar_cog=sar_path,
            out_path=out,
            colormap="viridis",
        )
        with rasterio.open(out) as src:
            assert src.count == 3

    def test_shape_mismatch_raises(self, tmp_path: Path) -> None:
        import pytest

        _, s2_path, _, _ = _make_s2_and_sar(tmp_path, shape=(64, 64))
        wrong_sar = write_db_cog(
            tmp_path / "sar_wrong.tif",
            np.zeros((32, 32), dtype=np.float32),
        )
        with pytest.raises(ValueError, match="does not match S2"):
            fuse(
                "sar_on_s2",
                s2_cog=s2_path,
                sar_cog=wrong_sar,
                out_path=tmp_path / "fused.tif",
            )
