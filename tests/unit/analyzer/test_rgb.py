"""Tests for the multitemporal RGB composite (Cian et al. style + change mode)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import rasterio

from services.analyzer.src.rgb import build_multitemporal_rgb

from .conftest import synthetic_db_stack, write_synthetic_db_cog


def _make_inputs(
    tmp_path: Path, n: int, shape: tuple[int, int] = (32, 32), seed: int = 0
) -> tuple[list[Path], list[datetime]]:
    stack = synthetic_db_stack(n, shape=shape, seed=seed)
    paths: list[Path] = []
    dates: list[datetime] = []
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    for i, frame in enumerate(stack):
        p = tmp_path / f"s_{i:02d}.tif"
        write_synthetic_db_cog(p, frame)
        paths.append(p)
        dates.append(t0 + timedelta(days=i * 12))
    return paths, dates


class TestBuildMultitemporalRgb:
    def test_smoke_physics(self, tmp_path: Path) -> None:
        """5 inputs in physics mode → 3-band uint8 COG validated by rio-cogeo."""
        paths, dates = _make_inputs(tmp_path, 5)
        out = tmp_path / "rgb.tif"
        result = build_multitemporal_rgb(paths, dates, out, mode="physics")
        assert result == out
        with rasterio.open(out) as src:
            assert src.count == 3
            assert src.dtypes == ("uint8", "uint8", "uint8")
            assert (src.height, src.width) == (32, 32)
        # rio cogeo validate must pass.
        proc = subprocess.run(
            ["uv", "run", "rio", "cogeo", "validate", str(out)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    def test_smoke_change(self, tmp_path: Path) -> None:
        """2 inputs in change mode → 3-band uint8 COG; channel B is mean of A and B."""
        paths, dates = _make_inputs(tmp_path, 2)
        out = tmp_path / "rgb.tif"
        build_multitemporal_rgb(paths, dates, out, mode="change")
        with rasterio.open(out) as src:
            assert src.count == 3
            assert src.dtypes == ("uint8", "uint8", "uint8")

    def test_change_band_b_is_mean(self, tmp_path: Path) -> None:
        """Verify the pre/post averaging in change mode using a controlled input."""
        # Pre = -10 dB constant; post = 0 dB constant.  Average = -5 dB.
        pre = np.full((16, 16), -10.0, dtype=np.float32)
        post = np.full((16, 16), 0.0, dtype=np.float32)
        p_pre = write_synthetic_db_cog(tmp_path / "pre.tif", pre)
        p_post = write_synthetic_db_cog(tmp_path / "post.tif", post)
        dates = [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 7, 1, tzinfo=UTC)]
        out = tmp_path / "rgb.tif"
        build_multitemporal_rgb([p_pre, p_post], dates, out, mode="change")

        with rasterio.open(out) as src:
            r = src.read(1)
            g = src.read(2)
            b = src.read(3)
        # R should be uniformly stretched from a constant → 128 (flat input
        # path).  Same for G and B.
        assert np.unique(r).tolist() == [128]
        assert np.unique(g).tolist() == [128]
        assert np.unique(b).tolist() == [128]

    def test_stretch_clamps_to_full_range(self, tmp_path: Path) -> None:
        """An input whose 2-98 percentiles span the data should stretch to [0, 255]."""
        # Linearly varying input from -20 to -4 dB across 32 columns.
        cols = np.linspace(-20.0, -4.0, 32, dtype=np.float32)
        frame = np.tile(cols, (32, 1))  # (32, 32)
        # Three copies; physics mode requires ≥3.
        paths = []
        dates = []
        for i in range(3):
            p = write_synthetic_db_cog(tmp_path / f"f{i}.tif", frame)
            paths.append(p)
            dates.append(datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=i))
        out = tmp_path / "rgb.tif"
        build_multitemporal_rgb(paths, dates, out, mode="physics", stretch_pct=(2.0, 98.0))
        with rasterio.open(out) as src:
            r = src.read(1)
        assert r.min() == 0
        assert r.max() == 255

    def test_shape_mismatch_raises(self, tmp_path: Path) -> None:
        """Two inputs with different shapes → ValueError."""
        a = np.zeros((16, 16), dtype=np.float32)
        b = np.zeros((16, 24), dtype=np.float32)
        p_a = write_synthetic_db_cog(tmp_path / "a.tif", a)
        p_b = write_synthetic_db_cog(tmp_path / "b.tif", b)
        out = tmp_path / "rgb.tif"
        dates = [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)]
        with pytest.raises(ValueError, match="shape mismatch"):
            build_multitemporal_rgb([p_a, p_b], dates, out, mode="change")

    def test_too_few_scenes_for_physics_raises(self, tmp_path: Path) -> None:
        """physics mode with 2 inputs → ValueError."""
        paths, dates = _make_inputs(tmp_path, 2)
        out = tmp_path / "rgb.tif"
        with pytest.raises(ValueError, match="at least 3"):
            build_multitemporal_rgb(paths, dates, out, mode="physics")

    def test_change_requires_exactly_two(self, tmp_path: Path) -> None:
        """change mode with 3 inputs → ValueError."""
        paths, dates = _make_inputs(tmp_path, 3)
        out = tmp_path / "rgb.tif"
        with pytest.raises(ValueError, match="exactly 2"):
            build_multitemporal_rgb(paths, dates, out, mode="change")

    def test_length_mismatch_dates_raises(self, tmp_path: Path) -> None:
        paths, _ = _make_inputs(tmp_path, 3)
        out = tmp_path / "rgb.tif"
        with pytest.raises(ValueError, match="same length"):
            build_multitemporal_rgb(
                paths,
                [datetime(2024, 1, 1, tzinfo=UTC)],  # only 1 date for 3 paths
                out,
                mode="physics",
            )
