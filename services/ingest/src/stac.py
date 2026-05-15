"""STAC catalog search against the CDSE catalog.

Why STAC (not OData) for search:
  - The CDSE STAC catalog is the recommended search interface for Copernicus
    products. It returns GeoJSON FeatureCollections with standard STAC item
    fields, making it easy to extract bbox, datetime, and product metadata
    without bespoke XML parsing.
  - Endpoint: https://catalogue.dataspace.copernicus.eu/stac/

Collections used:
  - SENTINEL-1  (GRD and SLC products)
  - SENTINEL-2  (S2MSI2A cloud-free basemap)
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
import structlog

from shared.geo import BBox
from shared.models import GeoJSON, Scene

log = structlog.get_logger(__name__)

_STAC_ROOT = "https://catalogue.dataspace.copernicus.eu/stac/"

# Map our platform codes to CDSE STAC collection names.
_PLATFORM_TO_COLLECTION: dict[str, str] = {
    "S1": "SENTINEL-1",
    "S2": "SENTINEL-2",
}

# Properties key where CDSE reports the product type (varies slightly by
# collection but "productType" / "s1:product_type" are the common ones).
_PRODUCT_TYPE_KEYS = (
    "productType",
    "s1:product_type",
    "s2:product_type",
    "eo:product_type",
)

# Polarization for S1 items.
_POLARIZATION_KEYS = (
    "polarisation",
    "sar:polarizations",
    "s1:polarisation",
    "sar:polarization",
)

# Orbit direction.
_ORBIT_DIRECTION_KEYS = (
    "orbit_direction",
    "sat:orbit_state",
    "orbitDirection",
    "s1:orbit_direction",
)


def _stable_scene_id(stac_item_id: str) -> UUID:
    """Derive a deterministic UUID from a STAC item ID.

    CDSE STAC item IDs are long strings like
    ``S1A_IW_GRDH_1SDV_20240101T...``.  We hash them to a UUID v5 so
    Scene.id is a proper UUID without storing a separate mapping table.
    """
    # UUID v5 with the OID namespace — stable as long as the STAC item ID is stable.
    namespace = UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")  # UUID namespace OID
    return UUID(bytes=hashlib.sha1((str(namespace) + stac_item_id).encode()).digest()[:16])


def _extract_polarization(properties: dict[str, Any]) -> str | None:
    """Pull polarization from STAC item properties.

    CDSE returns this as a string like "VV VH" or a list ["VV", "VH"].
    Normalise to a space-joined string so it's consistent downstream.
    """
    for key in _POLARIZATION_KEYS:
        val = properties.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            return " ".join(str(v) for v in val)
        return str(val)
    return None


def _extract_orbit_direction(
    properties: dict[str, Any],
) -> Literal["ASCENDING", "DESCENDING"] | None:
    for key in _ORBIT_DIRECTION_KEYS:
        val = properties.get(key)
        if val is None:
            continue
        val = str(val).upper()
        if val in ("ASCENDING", "DESCENDING"):
            return val  # type: ignore[return-value]
    return None


def _extract_product_type(properties: dict[str, Any]) -> str:
    for key in _PRODUCT_TYPE_KEYS:
        val = properties.get(key)
        if val is not None:
            return str(val)
    return "UNKNOWN"


def _parse_stac_item(item: dict[str, Any], platform: Literal["S1", "S2"]) -> Scene:
    """Convert a CDSE STAC feature dict to a ``Scene`` model."""
    props: dict[str, Any] = item.get("properties", {})
    geom: GeoJSON = item.get("geometry", {})

    sensing_start_raw = props.get("datetime") or props.get("start_datetime")
    sensing_end_raw = props.get("end_datetime") or sensing_start_raw

    # STAC datetimes are ISO 8601 strings; parse with datetime.fromisoformat.
    # Replace trailing 'Z' so Python 3.11's fromisoformat handles it correctly.
    def _parse_dt(s: str | None) -> datetime:
        if not s:
            raise ValueError("STAC item missing datetime property")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    return Scene(
        id=_stable_scene_id(item["id"]),
        platform=platform,
        product_type=_extract_product_type(props),
        sensing_start=_parse_dt(sensing_start_raw),
        sensing_end=_parse_dt(sensing_end_raw),
        polarization=_extract_polarization(props),
        orbit_direction=_extract_orbit_direction(props),
        footprint=geom,
    )


class STACClient:
    """Async STAC search client for the CDSE catalog.

    Wraps manual HTTP requests (not pystac-client) so we can:
    - Inject Bearer auth headers from CDSEAuth.
    - Use our own httpx.AsyncClient (which respects HTTPS_PROXY by default).
    - Record responses with vcrpy in tests.

    pystac-client is listed as a dependency because it may be used in future
    as a higher-level wrapper, but for now we drive STAC search manually.
    """

    def __init__(
        self,
        stac_root: str = _STAC_ROOT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Trust env proxy settings (HTTPS_PROXY, ALL_PROXY) — do not disable!
        self._stac_root = stac_root.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._own_client = client is None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def search(
        self,
        bbox: BBox,
        start: datetime,
        end: datetime,
        collection: str,
        product_type: str | None = None,
        auth_headers: dict[str, str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Execute a STAC POST /search request and return all matching items.

        Paginates via ``next`` link if the response contains more items than
        the page size. Returns raw STAC feature dicts; parsing into ``Scene``
        objects happens in the caller.
        """
        # STAC datetime interval: "start/end" in RFC 3339.
        dt_interval = f"{start.isoformat()}/{end.isoformat()}"

        body: dict[str, Any] = {
            "bbox": list(bbox),
            "datetime": dt_interval,
            "collections": [collection],
            "limit": limit,
        }
        if product_type:
            # CDSE supports CQL2-text or query-extension filtering but the
            # simplest supported path is to filter in Python post-fetch rather
            # than rely on catalog-specific filter extensions.
            # We keep the body simple and filter below.
            pass

        items: list[dict[str, Any]] = []
        url: str | None = f"{self._stac_root}/search"

        while url is not None:
            log.info(
                "cdse.stac.search",
                collection=collection,
                bbox=bbox,
                url=url,
            )
            response = await self._client.post(
                url,
                json=body,
                headers={**(auth_headers or {}), "Content-Type": "application/json"},
                timeout=30.0,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"CDSE STAC search returned {response.status_code}: {response.text}"
                )

            data: dict[str, Any] = response.json()
            page_items: list[dict[str, Any]] = data.get("features", [])

            # Filter by product type in Python (avoids relying on CQL extension).
            if product_type:
                page_items = [
                    f
                    for f in page_items
                    if _extract_product_type(f.get("properties", {})) == product_type
                ]

            items.extend(page_items)

            # Follow the STAC "next" link for pagination.
            next_link = next(
                (lnk for lnk in data.get("links", []) if lnk.get("rel") == "next"),
                None,
            )
            url = next_link["href"] if next_link else None
            # Reset body to empty for GET-based next links (some catalogs use GET).
            body = {}

        log.info("cdse.stac.done", collection=collection, n_results=len(items))
        return items

    async def search_scenes(
        self,
        bbox: BBox,
        start: datetime,
        end: datetime,
        platform: Literal["S1", "S2"],
        product_type: str | None = None,
        auth_headers: dict[str, str] | None = None,
    ) -> list[Scene]:
        """High-level search returning parsed ``Scene`` objects."""
        collection = _PLATFORM_TO_COLLECTION[platform]
        raw_items = await self.search(
            bbox=bbox,
            start=start,
            end=end,
            collection=collection,
            product_type=product_type,
            auth_headers=auth_headers,
        )
        scenes = [_parse_stac_item(item, platform) for item in raw_items]
        log.info(
            "cdse.stac.parsed",
            platform=platform,
            n_scenes=len(scenes),
        )
        return scenes
