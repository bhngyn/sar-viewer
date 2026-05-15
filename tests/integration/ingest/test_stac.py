"""Integration tests for CDSE STAC search.

All HTTP interactions are mocked with respx (httpx-native mock transport).
No live network calls are made in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from services.ingest.src.stac import STACClient, _parse_stac_item, _stable_scene_id

_STAC_SEARCH_URL = "https://catalogue.dataspace.copernicus.eu/stac/search"

# Representative STAC items that mirror real CDSE responses.
_S1_ITEM: dict[str, Any] = {
    "type": "Feature",
    "stac_version": "1.0.0",
    "id": "S1A_IW_GRDH_1SDV_20240115T054012_20240115T054037_052115_064E12",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[13.0, 51.0], [14.0, 51.0], [14.0, 52.0], [13.0, 52.0], [13.0, 51.0]]],
    },
    "bbox": [13.0, 51.0, 14.0, 52.0],
    "properties": {
        "datetime": "2024-01-15T05:40:12Z",
        "start_datetime": "2024-01-15T05:40:12Z",
        "end_datetime": "2024-01-15T05:40:37Z",
        "platform": "SENTINEL-1A",
        "productType": "IW_GRDH_1S",
        "polarisation": "VV VH",
        "orbitDirection": "ASCENDING",
    },
    "links": [],
    "assets": {},
}

_S2_ITEM: dict[str, Any] = {
    "type": "Feature",
    "stac_version": "1.0.0",
    "id": "S2A_MSIL2A_20240115T095251_N0510_R079_T33UVT_20240115T133441",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[13.0, 51.0], [14.0, 51.0], [14.0, 52.0], [13.0, 52.0], [13.0, 51.0]]],
    },
    "bbox": [13.0, 51.0, 14.0, 52.0],
    "properties": {
        "datetime": "2024-01-15T09:52:51Z",
        "start_datetime": "2024-01-15T09:52:51Z",
        "end_datetime": "2024-01-15T09:52:51Z",
        "platform": "SENTINEL-2A",
        "productType": "S2MSI2A",
    },
    "links": [],
    "assets": {},
}


def _s1_collection_response(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": list(items),
        "links": [],
        "numberMatched": len(items),
        "numberReturned": len(items),
    }


def test_stable_scene_id_deterministic() -> None:
    """Same STAC item ID always produces the same UUID."""
    item_id = "S1A_IW_GRDH_1SDV_20240115T054012"
    uid1 = _stable_scene_id(item_id)
    uid2 = _stable_scene_id(item_id)
    assert uid1 == uid2


def test_stable_scene_id_different_for_different_items() -> None:
    """Different STAC item IDs produce different UUIDs."""
    uid1 = _stable_scene_id("S1A_IW_GRDH_1SDV_20240115T054012")
    uid2 = _stable_scene_id("S1B_IW_GRDH_1SDV_20240121T054012")
    assert uid1 != uid2


def test_parse_stac_item_s1() -> None:
    """Parsing an S1 STAC item produces a valid Scene model."""
    scene = _parse_stac_item(_S1_ITEM, "S1")
    assert scene.platform == "S1"
    assert scene.product_type == "IW_GRDH_1S"
    assert scene.polarization == "VV VH"
    assert scene.orbit_direction == "ASCENDING"
    assert scene.sensing_start == datetime(2024, 1, 15, 5, 40, 12, tzinfo=UTC)
    assert scene.sensing_end == datetime(2024, 1, 15, 5, 40, 37, tzinfo=UTC)
    # Footprint should be the GeoJSON geometry.
    assert scene.footprint["type"] == "Polygon"


def test_parse_stac_item_s2() -> None:
    """Parsing an S2 STAC item produces a valid Scene model with no polarization."""
    scene = _parse_stac_item(_S2_ITEM, "S2")
    assert scene.platform == "S2"
    assert scene.product_type == "S2MSI2A"
    assert scene.polarization is None
    assert scene.orbit_direction is None


@pytest.mark.asyncio
@respx.mock
async def test_search_scenes_s1() -> None:
    """search_scenes returns parsed Scene objects for an S1 collection search."""
    respx.post(_STAC_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_s1_collection_response(_S1_ITEM))
    )

    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 31, tzinfo=UTC)
    bbox = (13.0, 51.0, 14.0, 52.0)

    async with httpx.AsyncClient() as client:
        stac = STACClient(client=client)
        scenes = await stac.search_scenes(
            bbox=bbox,
            start=start,
            end=end,
            platform="S1",
        )

    assert len(scenes) == 1
    assert scenes[0].platform == "S1"
    assert scenes[0].product_type == "IW_GRDH_1S"
    # Round-trip through Pydantic.
    from shared.models import Scene

    Scene.model_validate_json(scenes[0].model_dump_json())


@pytest.mark.asyncio
@respx.mock
async def test_search_scenes_product_type_filter() -> None:
    """product_type filter removes non-matching items from results."""
    # Mix of GRD and SLC items.
    slc_item = {
        **_S1_ITEM,
        "id": "SLC_item",
        "properties": {**_S1_ITEM["properties"], "productType": "IW_SLC__1S"},
    }
    respx.post(_STAC_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_s1_collection_response(_S1_ITEM, slc_item))
    )

    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 31, tzinfo=UTC)

    async with httpx.AsyncClient() as client:
        stac = STACClient(client=client)
        scenes = await stac.search_scenes(
            bbox=(13.0, 51.0, 14.0, 52.0),
            start=start,
            end=end,
            platform="S1",
            product_type="IW_GRDH_1S",  # only GRD
        )

    assert len(scenes) == 1
    assert scenes[0].product_type == "IW_GRDH_1S"


@pytest.mark.asyncio
@respx.mock
async def test_search_scenes_http_error_raises() -> None:
    """A non-200 STAC response raises RuntimeError."""
    respx.post(_STAC_SEARCH_URL).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 31, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="500"):
        async with httpx.AsyncClient() as client:
            stac = STACClient(client=client)
            await stac.search_scenes(
                bbox=(13.0, 51.0, 14.0, 52.0),
                start=start,
                end=end,
                platform="S1",
            )
