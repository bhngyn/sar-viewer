"""Integration tests for the CDSE OData download client.

All HTTP interactions are mocked with respx.  Zip extraction is tested with
an in-memory zip created during the test (no live network calls).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx

from services.ingest.src.odata import ODataClient, ODataDownloadError

_ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1"


def _make_fake_zip_bytes(
    filename: str = "test.txt", content: bytes = b"fake SAFE content"
) -> bytes:
    """Build a minimal in-memory zip file for testing the download + unpack path."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


@pytest.mark.asyncio
@respx.mock
async def test_download_scene_success(tmp_path: Path) -> None:
    """Successful download unpacks the zip to the cache directory."""
    scene_id = uuid4()
    cdse_product_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    fake_zip = _make_fake_zip_bytes("S1A.SAFE/manifest.safe", b"<manifest/>")

    url = f"{_ODATA_BASE}/Products({cdse_product_id})/$value"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=fake_zip,
            headers={"Content-Type": "application/zip"},
        )
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        odata = ODataClient(cache_root=tmp_path, client=client)
        result = await odata.download_scene(
            scene_id=scene_id,
            cdse_product_id=cdse_product_id,
            auth_headers={"Authorization": "Bearer fake"},
        )

    assert result == tmp_path / str(scene_id)
    assert result.exists()
    # The inner file should be present after unpack.
    assert (result / "S1A.SAFE" / "manifest.safe").exists()


@pytest.mark.asyncio
@respx.mock
async def test_download_scene_idempotent(tmp_path: Path) -> None:
    """A second call to download_scene for an already-cached scene skips download."""
    scene_id = uuid4()
    cdse_product_id = str(uuid4())
    target = tmp_path / str(scene_id)
    target.mkdir()
    (target / "manifest.safe").write_bytes(b"cached")

    # No mock registered — if a request were made, respx would raise.
    async with httpx.AsyncClient(follow_redirects=True) as client:
        odata = ODataClient(cache_root=tmp_path, client=client)
        result = await odata.download_scene(
            scene_id=scene_id,
            cdse_product_id=cdse_product_id,
            auth_headers={"Authorization": "Bearer fake"},
        )

    assert result == target


@pytest.mark.asyncio
@respx.mock
async def test_download_scene_http_error_raises(tmp_path: Path) -> None:
    """A non-200 response raises ODataDownloadError."""
    scene_id = uuid4()
    cdse_product_id = str(uuid4())
    url = f"{_ODATA_BASE}/Products({cdse_product_id})/$value"

    respx.get(url).mock(return_value=httpx.Response(403, text="Forbidden"))

    with pytest.raises(ODataDownloadError, match="403"):
        async with httpx.AsyncClient(follow_redirects=True) as client:
            odata = ODataClient(cache_root=tmp_path, client=client)
            await odata.download_scene(
                scene_id=scene_id,
                cdse_product_id=cdse_product_id,
                auth_headers={"Authorization": "Bearer fake"},
            )

    # On failure, the cache directory should not be left partially populated.
    assert not (tmp_path / str(scene_id)).exists() or not any((tmp_path / str(scene_id)).iterdir())


def test_scene_cache_dir(tmp_path: Path) -> None:
    """scene_cache_dir returns the expected path."""
    odata = ODataClient(cache_root=tmp_path)
    scene_id = uuid4()
    assert odata.scene_cache_dir(scene_id) == tmp_path / str(scene_id)


def test_is_cached_false_when_missing(tmp_path: Path) -> None:
    """_is_cached returns False for a non-existent scene directory."""
    odata = ODataClient(cache_root=tmp_path)
    assert not odata._is_cached(uuid4())


def test_is_cached_false_when_empty(tmp_path: Path) -> None:
    """_is_cached returns False for an empty directory."""
    scene_id = uuid4()
    (tmp_path / str(scene_id)).mkdir()
    odata = ODataClient(cache_root=tmp_path)
    assert not odata._is_cached(scene_id)


def test_is_cached_true_when_has_content(tmp_path: Path) -> None:
    """_is_cached returns True for a non-empty directory."""
    scene_id = uuid4()
    d = tmp_path / str(scene_id)
    d.mkdir()
    (d / "something.safe").write_bytes(b"data")
    odata = ODataClient(cache_root=tmp_path)
    assert odata._is_cached(scene_id)
