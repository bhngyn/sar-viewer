"""anomaly_highlight mode: bbox overlays in red/cyan/yellow."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import rasterio

from services.fusion.src.fuse import fuse
from shared.models import Anomaly

from .conftest import write_rgb_cog


def _make_s2(tmp_path: Path) -> Path:
    """A uniform-grey S2 basemap so anomaly fills stand out cleanly."""
    s2 = np.full((64, 64, 3), 100, dtype=np.uint8)
    return write_rgb_cog(tmp_path / "s2.tif", s2)


class TestFuseAnomalyHighlight:
    def test_new_anomaly_increases_red_inside_bbox(self, tmp_path: Path) -> None:
        s2_path = _make_s2(tmp_path)
        # The synthetic S2 uses pixel_size_m=10, origin (0, 1000).  Build a
        # bbox covering rows 20:40, cols 20:40 → geographic
        # (minx=200, miny=600, maxx=400, maxy=800) in EPSG:32633.
        anomaly = Anomaly(
            id=uuid.uuid4(),
            aoi_id=uuid.uuid4(),
            scene_id=uuid.uuid4(),
            bbox=(200.0, 600.0, 400.0, 800.0),
            score=10.0,
            kind="new",
        )
        out = tmp_path / "fused.tif"
        fuse(
            "anomaly_highlight",
            s2_cog=s2_path,
            anomalies=[anomaly],
            out_path=out,
            watermark=False,
        )
        with rasterio.open(out) as src:
            r = src.read(1)
            g = src.read(2)
            b = src.read(3)

        # Outside the bbox, channels are still ~100 (the basemap value).
        assert r[5, 5] == 100
        # Inside the bbox interior, red channel should be higher (anomaly fill
        # blended 25% with the red #d62728 = (214, 39, 40)).
        # Picking a pixel strictly inside the bbox (away from outline).
        inner_r = r[25, 25]
        inner_g = g[25, 25]
        inner_b = b[25, 25]
        assert inner_r > 100, inner_r  # red is higher than basemap
        assert inner_g < 100, inner_g  # green is lower
        assert inner_b < 100, inner_b  # blue is lower

    def test_missing_uses_cyan(self, tmp_path: Path) -> None:
        s2_path = _make_s2(tmp_path)
        anomaly = Anomaly(
            id=uuid.uuid4(),
            aoi_id=uuid.uuid4(),
            scene_id=uuid.uuid4(),
            bbox=(200.0, 600.0, 400.0, 800.0),
            score=10.0,
            kind="missing",
        )
        out = tmp_path / "fused.tif"
        fuse(
            "anomaly_highlight",
            s2_cog=s2_path,
            anomalies=[anomaly],
            out_path=out,
            watermark=False,
        )
        with rasterio.open(out) as src:
            r = src.read(1)
            g = src.read(2)
            b = src.read(3)
        # Cyan #17becf = (23, 190, 207).  Red ↓, green ↑, blue ↑.
        assert r[25, 25] < 100
        assert g[25, 25] > 100
        assert b[25, 25] > 100

    def test_empty_list_just_returns_basemap_with_watermark(self, tmp_path: Path) -> None:
        s2_path = _make_s2(tmp_path)
        out = tmp_path / "fused.tif"
        fuse(
            "anomaly_highlight",
            s2_cog=s2_path,
            anomalies=[],
            out_path=out,
            watermark=False,
        )
        with rasterio.open(out) as src:
            r = src.read(1)
        # No anomalies → uniform basemap.
        assert (r == 100).all()
