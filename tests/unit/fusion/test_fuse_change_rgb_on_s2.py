"""change_rgb_on_s2 mode: enforces alpha ≤ 0.5 (basemap context)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from services.fusion.src.fuse import fuse

from .conftest import write_rgb_cog


def _make_s2_and_rgb(tmp_path: Path, shape: tuple[int, int] = (32, 32)):
    rng = np.random.default_rng(0)
    s2 = rng.integers(0, 255, size=(*shape, 3), dtype=np.uint8)
    rgb = rng.integers(0, 255, size=(*shape, 3), dtype=np.uint8)
    s2_path = write_rgb_cog(tmp_path / "s2.tif", s2)
    rgb_path = write_rgb_cog(tmp_path / "rgb.tif", rgb)
    return s2_path, rgb_path


class TestFuseChangeRgbOnS2:
    def test_alpha_at_max_succeeds(self, tmp_path: Path) -> None:
        s2_path, rgb_path = _make_s2_and_rgb(tmp_path)
        out = tmp_path / "fused.tif"
        fuse(
            "change_rgb_on_s2",
            s2_cog=s2_path,
            rgb_cog=rgb_path,
            out_path=out,
            alpha=0.5,
        )
        assert out.exists()

    def test_alpha_above_max_raises(self, tmp_path: Path) -> None:
        s2_path, rgb_path = _make_s2_and_rgb(tmp_path)
        with pytest.raises(ValueError, match="alpha must be"):
            fuse(
                "change_rgb_on_s2",
                s2_cog=s2_path,
                rgb_cog=rgb_path,
                out_path=tmp_path / "fused.tif",
                alpha=0.6,
            )

    def test_missing_rgb_raises(self, tmp_path: Path) -> None:
        s2_path, _ = _make_s2_and_rgb(tmp_path)
        with pytest.raises(ValueError, match="requires rgb_cog"):
            fuse(
                "change_rgb_on_s2",
                s2_cog=s2_path,
                out_path=tmp_path / "fused.tif",
                alpha=0.5,
            )
