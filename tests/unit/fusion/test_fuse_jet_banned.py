"""Defensive: ``colormap="jet"`` is rejected even if Literal is bypassed."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from services.fusion.src.fuse import fuse

from .conftest import write_db_cog, write_rgb_cog


class TestFuseJetBanned:
    def test_jet_raises_value_error(self, tmp_path: Path) -> None:
        s2 = np.full((16, 16, 3), 100, dtype=np.uint8)
        sar = np.zeros((16, 16), dtype=np.float32)
        s2_path = write_rgb_cog(tmp_path / "s2.tif", s2)
        sar_path = write_db_cog(tmp_path / "sar.tif", sar)
        with pytest.raises(ValueError, match="banned"):
            fuse(
                "sar_on_s2",
                s2_cog=s2_path,
                sar_cog=sar_path,
                out_path=tmp_path / "fused.tif",
                # Type checker would reject this; the runtime guard must too.
                colormap="jet",  # type: ignore[arg-type]
            )
