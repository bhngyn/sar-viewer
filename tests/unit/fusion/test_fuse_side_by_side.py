"""side_by_side mode: passthrough."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from services.fusion.src.fuse import fuse

from .conftest import write_db_cog, write_rgb_cog


class TestFuseSideBySide:
    def test_returns_sar_unchanged(self, tmp_path: Path) -> None:
        s2 = np.full((8, 8, 3), 100, dtype=np.uint8)
        sar = np.zeros((8, 8), dtype=np.float32)
        s2_path = write_rgb_cog(tmp_path / "s2.tif", s2)
        sar_path = write_db_cog(tmp_path / "sar.tif", sar)
        out_path = tmp_path / "should_not_exist.tif"
        returned = fuse(
            "side_by_side",
            s2_cog=s2_path,
            sar_cog=sar_path,
            out_path=out_path,
        )
        assert returned == sar_path  # passthrough
        assert not out_path.exists()  # no file written
