"""Geometry helpers: bbox extraction, reprojection, XYZ tile bounds."""

from __future__ import annotations

import math
from typing import Any

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

GeoJSON = dict[str, Any]
BBox = tuple[float, float, float, float]


def bbox_from_geojson(geom: GeoJSON) -> BBox:
    """Return the (minx, miny, maxx, maxy) bounding box of a GeoJSON geometry."""
    g = shape(geom)
    minx, miny, maxx, maxy = g.bounds
    return (float(minx), float(miny), float(maxx), float(maxy))


def reproject(geom: GeoJSON, src_crs: str, dst_crs: str) -> GeoJSON:
    """Reproject a GeoJSON geometry between coordinate reference systems.

    CRS strings follow pyproj conventions (e.g. ``"EPSG:4326"``, ``"EPSG:3857"``).
    """
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    g = shape(geom)
    reprojected = shapely_transform(transformer.transform, g)
    return reprojected.__geo_interface__  # type: ignore[no-any-return]


def tile_bounds(z: int, x: int, y: int) -> BBox:
    """Return the lon/lat bounds of a standard XYZ web-mercator tile.

    Uses the Slippy-map / OSM convention (origin top-left, y increases southward).
    """
    n = 2.0**z
    lon_west = x / n * 360.0 - 180.0
    lon_east = (x + 1) / n * 360.0 - 180.0
    lat_north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return (lon_west, lat_south, lon_east, lat_north)
