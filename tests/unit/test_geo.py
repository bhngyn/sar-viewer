"""Smoke tests for shared.geo helpers."""

from __future__ import annotations

import math

from shared.geo import bbox_from_geojson, reproject, tile_bounds


def test_bbox_from_geojson_polygon() -> None:
    geom = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 3], [0, 3], [0, 0]]],
    }
    assert bbox_from_geojson(geom) == (0.0, 0.0, 2.0, 3.0)


def test_reproject_wgs84_to_web_mercator() -> None:
    point = {"type": "Point", "coordinates": [0.0, 0.0]}
    reprojected = reproject(point, "EPSG:4326", "EPSG:3857")
    x, y = reprojected["coordinates"]
    assert math.isclose(x, 0.0, abs_tol=1e-6)
    assert math.isclose(y, 0.0, abs_tol=1e-6)


def test_tile_bounds_z0() -> None:
    minx, miny, maxx, maxy = tile_bounds(0, 0, 0)
    assert math.isclose(minx, -180.0)
    assert math.isclose(maxx, 180.0)
    # Web mercator clipping latitude.
    assert math.isclose(miny, -85.0511287798, abs_tol=1e-6)
    assert math.isclose(maxy, 85.0511287798, abs_tol=1e-6)
