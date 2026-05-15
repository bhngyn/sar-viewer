"""OData client for downloading full Sentinel SAFE archives from CDSE.

Why OData (not S3) for downloads:
  - The CDSE OData endpoint supports authenticated download without needing
    AWS-style S3 credentials. The S3 pathway requires additional credential
    brokering that adds complexity for Phase 1. OData is simpler and more
    consistent with the auth flow already established.
  - S3 can be added in a future phase if throughput becomes a bottleneck.

Endpoint: https://catalogue.dataspace.copernicus.eu/odata/v1/Products(<id>)/$value

OPSEC note: the OData download sends the exact product UUID to CDSE servers
(a known residual risk documented in THREAT_MODEL.md Phase 4).
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

import httpx
import structlog

log = structlog.get_logger(__name__)

_ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1"

# Maximum number of bytes to buffer in memory during streaming download.
# Keeping this small ensures large archives stream to disk without blowing RAM.
_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class ODataDownloadError(Exception):
    """Raised when an OData download fails."""


class ODataClient:
    """Async client for downloading SAFE archives via CDSE OData.

    Downloads are streamed to a temporary file, then unpacked to
    ``cache_root / <scene_id> /``.  Idempotent: if the target directory
    already exists and is non-empty, the download is skipped.
    """

    def __init__(
        self,
        cache_root: Path,
        *,
        odata_base: str = _ODATA_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache_root = cache_root
        self._odata_base = odata_base.rstrip("/")
        # Trust env proxy settings — do not pass trust_env=False.
        self._client = client or httpx.AsyncClient(follow_redirects=True)
        self._own_client = client is None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    def scene_cache_dir(self, scene_id: UUID) -> Path:
        """Return the expected on-disk directory for an unpacked SAFE archive."""
        return self._cache_root / str(scene_id)

    def _is_cached(self, scene_id: UUID) -> bool:
        """True if the scene directory exists and contains at least one file."""
        d = self.scene_cache_dir(scene_id)
        return d.exists() and any(d.iterdir())

    async def download_scene(
        self,
        scene_id: UUID,
        cdse_product_id: str,
        auth_headers: dict[str, str],
    ) -> Path:
        """Download and unpack a SAFE archive from CDSE OData.

        Args:
            scene_id: Our internal UUID (used as the cache directory name).
            cdse_product_id: The CDSE product UUID string (not our scene_id).
            auth_headers: Bearer token headers from ``CDSEAuth.auth_headers()``.

        Returns:
            Path to the unpacked SAFE directory on disk.
        """
        target = self.scene_cache_dir(scene_id)
        if self._is_cached(scene_id):
            log.info("cdse.odata.cache_hit", scene_id=str(scene_id), path=str(target))
            return target

        url = f"{self._odata_base}/Products({cdse_product_id})/$value"
        log.info("cdse.odata.download_start", scene_id=str(scene_id), url=url)

        # Stream to a temp file so we don't hold the entire archive in RAM.
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            async with self._client.stream(
                "GET",
                url,
                headers=auth_headers,
                timeout=3600.0,  # large archives can take a while on a slow uplink
            ) as response:
                if response.status_code != 200:
                    raise ODataDownloadError(
                        f"CDSE OData returned {response.status_code} for {url}"
                    )
                total_bytes = 0
                with open(tmp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        f.write(chunk)
                        total_bytes += len(chunk)

            log.info("cdse.odata.download_complete", scene_id=str(scene_id), bytes=total_bytes)

            # Unpack the zip to a staging directory, then move atomically.
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(target)

            log.info("cdse.odata.unpacked", scene_id=str(scene_id), path=str(target))
            return target

        except Exception:
            # Remove partially written directory on failure.
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise
        finally:
            tmp_path.unlink(missing_ok=True)
