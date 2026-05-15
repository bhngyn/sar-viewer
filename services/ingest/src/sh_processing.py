"""Sentinel Hub Processing API client for on-the-fly cropped quicklook previews.

Why Sentinel Hub for previews (not the full CDSE OData download):
  - SH Processing API returns a cropped, calibrated PNG/GeoTIFF for a given
    bbox in seconds — ideal for the UI thumbnail before the operator commits
    to downloading a multi-GB SAFE archive.
  - Full downloads (OData/S3) are handled by ``odata.py``.

Outbound destination: https://services.sentinel-hub.com (Sentinel Hub API).
This was confirmed as an approved destination alongside *.dataspace.copernicus.eu.
SH_INSTANCE_ID comes from the operator's .env file.

OPSEC: SH_INSTANCE_ID is read from environment; never hardcoded.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_SH_PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"


class SHProcessingError(Exception):
    """Raised when the SH Processing API returns an error."""


class SHProcessingClient:
    """Client for the Sentinel Hub Processing API.

    Fetches a JPEG/PNG preview for a given bbox + datetime window.
    The SH instance_id is passed as a query parameter on the URL (no OAuth
    required for raster-delivery requests when using instance_id auth).

    NOTE: SH also supports OAuth2 bearer tokens; if the operator switches to
    OAuth2-based auth in a future phase, that change goes through the
    interface-change request protocol.
    """

    def __init__(
        self,
        instance_id: str | None = None,
        *,
        process_url: str = _SH_PROCESS_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Read from env if not provided explicitly (tests can inject directly).
        self._instance_id = instance_id or os.environ.get("SH_INSTANCE_ID", "")
        self._process_url = process_url
        self._client = client or httpx.AsyncClient()
        self._own_client = client is None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    def _make_evalscript_true_color(self) -> str:
        """Return a minimal Sentinel-2 true-color evalscript."""
        return """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04", "B03", "B02"], units: "DN" }],
    output: { bands: 3 }
  };
}
function evaluatePixel(sample) {
  return [sample.B04 / 10000.0, sample.B03 / 10000.0, sample.B02 / 10000.0];
}
"""

    def _make_evalscript_sar_vv(self) -> str:
        """Return a minimal Sentinel-1 VV backscatter evalscript."""
        return """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV"], units: "SIGMA0_ELLIPSOID" }],
    output: { bands: 1 }
  };
}
function evaluatePixel(sample) {
  // Linear to dB, clipped to [-25, 0] dB range for visual contrast.
  var db = 10 * Math.log10(Math.max(sample.VV, 1e-10));
  return [(db + 25) / 25.0];
}
"""

    async def get_preview(
        self,
        bbox: tuple[float, float, float, float],
        start_date: str,
        end_date: str,
        *,
        platform: str = "S2",
        width: int = 512,
        height: int = 512,
    ) -> bytes:
        """Fetch a PNG preview from the Sentinel Hub Processing API.

        Args:
            bbox: (minx, miny, maxx, maxy) in WGS-84.
            start_date: ISO date string, e.g. "2024-01-01".
            end_date: ISO date string.
            platform: "S1" for SAR VV or "S2" for true-color optical.
            width: output raster width in pixels.
            height: output raster height in pixels.

        Returns:
            PNG image bytes.
        """
        if not self._instance_id:
            raise SHProcessingError(
                "SH_INSTANCE_ID must be set in the environment to use the preview API."
            )

        if platform == "S2":
            data_source = "S2L2A"
            evalscript = self._make_evalscript_true_color()
        else:
            data_source = "S1GRD"
            evalscript = self._make_evalscript_sar_vv()

        request_body: dict[str, Any] = {
            "input": {
                "bounds": {
                    "bbox": list(bbox),
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [
                    {
                        "dataFilter": {
                            "timeRange": {
                                "from": f"{start_date}T00:00:00Z",
                                "to": f"{end_date}T23:59:59Z",
                            },
                            "mosaickingOrder": "leastCC",
                        },
                        "type": data_source,
                    }
                ],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
            },
            "evalscript": evalscript,
        }

        url = f"{self._process_url}?instanceId={self._instance_id}"

        log.info(
            "sh.preview.fetch",
            platform=platform,
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
        )

        response = await self._client.post(
            url,
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

        if response.status_code != 200:
            raise SHProcessingError(
                f"SH Processing API returned {response.status_code}: {response.text}"
            )

        log.info("sh.preview.done", content_length=len(response.content))
        return response.content
