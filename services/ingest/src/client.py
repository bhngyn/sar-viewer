"""Public ingest API — the three functions that Agent C's worker tasks call.

Interface contract (from phase-1-brief.md §2.2 — DO NOT change signatures):

    async def search_scenes(
        aoi: AOI,
        start: datetime,
        end: datetime,
        platform: Literal["S1", "S2"],
        product_type: str | None = None,
    ) -> list[Scene]: ...

    async def fetch_scene(scene_id: UUID) -> Path: ...
        # returns local path to the unpacked .SAFE directory

    async def preview_scene(
        scene_id: UUID,
        bbox: tuple[float, float, float, float],
    ) -> bytes: ...
        # returns PNG bytes for UI thumbnail

All three functions are async; callers are expected to run them in an asyncio
event loop (Celery tasks call asyncio.run()).

OPSEC:
  - AOI coordinates are redacted in logs unless LOG_AOI=true.
  - HTTP clients inherit proxy settings from the environment (trust_env=True).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import structlog

from shared.geo import bbox_from_geojson
from shared.models import AOI, Scene

from .auth import CDSEAuth
from .db import get_scene_by_id, init_db, upsert_scene
from .odata import ODataClient
from .sh_processing import SHProcessingClient
from .stac import STACClient

log = structlog.get_logger(__name__)


def _log_aoi(aoi: AOI) -> str:
    """Return either the hashed or plain geometry string for logging."""
    geom_str = json.dumps(aoi.geometry, separators=(",", ":"), sort_keys=True)
    if os.environ.get("LOG_AOI", "false").lower() == "true":
        return geom_str
    return hashlib.sha256(geom_str.encode()).hexdigest()[:12]


# --- Shared auth + HTTP client singletons ------------------------------------
# These are module-level so they can be shared across calls within a single
# Celery task process (token is reused across concurrent requests).

_auth = CDSEAuth()
# A single AsyncClient is created per process; it is reused across requests
# for connection-pooling efficiency. Proxy settings are inherited from env.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        # trust_env=True (default) ensures HTTPS_PROXY / ALL_PROXY are honoured.
        _http_client = httpx.AsyncClient(follow_redirects=True)
    return _http_client


# Default data directories — callers can pass overrides for testing.
_DEFAULT_CACHE_ROOT = Path(os.environ.get("INGEST_CACHE_ROOT", "data/cache"))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def search_scenes(
    aoi: AOI,
    start: datetime,
    end: datetime,
    platform: Literal["S1", "S2"],
    product_type: str | None = None,
) -> list[Scene]:
    """Search CDSE STAC for scenes covering ``aoi`` in the given time range.

    Persists discovered scene metadata to SQLite so subsequent calls to
    ``fetch_scene`` and ``preview_scene`` can look up CDSE product IDs.

    Returns:
        A list of ``Scene`` objects sorted by sensing_start ascending.
    """
    await init_db()  # no-op if table already exists

    log.info(
        "ingest.search_scenes",
        aoi_hash=_log_aoi(aoi),
        start=start.isoformat(),
        end=end.isoformat(),
        platform=platform,
        product_type=product_type,
    )

    client = _get_http_client()
    auth_headers = await _auth.auth_headers(client)
    bbox = bbox_from_geojson(aoi.geometry)

    stac = STACClient(client=client)
    scenes = await stac.search_scenes(
        bbox=bbox,
        start=start,
        end=end,
        platform=platform,
        product_type=product_type,
        auth_headers=auth_headers,
    )

    # Persist to SQLite.  We use the STAC item id (inside the scene footprint
    # properties, if available) as the cdse_product_id; for now we store None
    # and rely on future enrichment from the OData catalogue if needed.
    for scene in scenes:
        await upsert_scene(
            scene_id=scene.id,
            platform=scene.platform,
            product_type=scene.product_type,
            sensing_start=scene.sensing_start,
            sensing_end=scene.sensing_end,
            footprint_json=json.dumps(scene.footprint),
            polarization=scene.polarization,
            orbit_direction=scene.orbit_direction,
        )

    scenes_sorted = sorted(scenes, key=lambda s: s.sensing_start)
    log.info("ingest.search_scenes.done", n_scenes=len(scenes_sorted))
    return scenes_sorted


async def fetch_scene(
    scene_id: UUID,
    *,
    cache_root: Path | None = None,
    cdse_product_id: str | None = None,
) -> Path:
    """Download and unpack the SAFE archive for ``scene_id`` to local disk.

    Idempotent: if the scene is already cached the download is skipped.

    Args:
        scene_id: Our internal UUID for the scene.
        cache_root: Override the default data/cache root (useful in tests).
        cdse_product_id: The CDSE OData product UUID string. If None, looked
            up from the local SQLite database (must have been inserted by
            ``search_scenes`` first).

    Returns:
        Path to the unpacked .SAFE directory on disk.
    """
    root = cache_root or _DEFAULT_CACHE_ROOT

    # Look up the CDSE product ID if not provided.
    if cdse_product_id is None:
        row = await get_scene_by_id(scene_id)
        if row is None:
            raise FileNotFoundError(
                f"Scene {scene_id} not found in local database. "
                "Run search_scenes first to populate the catalogue."
            )
        cdse_product_id = row.get("cdse_product_id") or str(scene_id)

    log.info("ingest.fetch_scene", scene_id=str(scene_id))

    client = _get_http_client()
    auth_headers = await _auth.auth_headers(client)

    odata = ODataClient(cache_root=root, client=client)
    path = await odata.download_scene(
        scene_id=scene_id,
        cdse_product_id=cdse_product_id,
        auth_headers=auth_headers,
    )

    log.info("ingest.fetch_scene.done", scene_id=str(scene_id), path=str(path))
    return path


async def preview_scene(
    scene_id: UUID,
    bbox: tuple[float, float, float, float],
) -> bytes:
    """Fetch a PNG thumbnail for ``scene_id`` from the Sentinel Hub Processing API.

    Args:
        scene_id: Our internal UUID for the scene.
        bbox: (minx, miny, maxx, maxy) in WGS-84 for the area to crop.

    Returns:
        PNG image bytes suitable for the UI thumbnail.
    """
    log.info("ingest.preview_scene", scene_id=str(scene_id))

    # Look up the scene to determine platform and sensing date window.
    row = await get_scene_by_id(scene_id)
    if row is None:
        raise FileNotFoundError(
            f"Scene {scene_id} not found in local database. Run search_scenes first."
        )

    platform = row["platform"]
    # Use sensing_start/sensing_end as the SH time range.
    start_date = row["sensing_start"][:10]  # ISO date "YYYY-MM-DD"
    end_date = row["sensing_end"][:10]

    sh_client = SHProcessingClient(client=_get_http_client())
    png_bytes = await sh_client.get_preview(
        bbox=bbox,
        start_date=start_date,
        end_date=end_date,
        platform=platform,
    )

    log.info("ingest.preview_scene.done", scene_id=str(scene_id), bytes=len(png_bytes))
    return png_bytes
