"""Integration tests for Sentinel Hub Processing API preview client.

All HTTP interactions are mocked with respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from services.ingest.src.sh_processing import SHProcessingClient, SHProcessingError

_SH_PROCESS_URL = "https://services.sentinel-hub.com/api/v1/process"

# A minimal valid PNG (1x1 white pixel).
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"  # signature
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde"  # IHDR chunk (1x1, 8-bit RGB)
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
@respx.mock
async def test_get_preview_s2_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """S2 preview returns PNG bytes on a 200 response."""
    monkeypatch.setenv("SH_INSTANCE_ID", "test-instance-id")

    respx.post(url__startswith=_SH_PROCESS_URL).mock(
        return_value=httpx.Response(200, content=_TINY_PNG, headers={"Content-Type": "image/png"})
    )

    async with httpx.AsyncClient() as client:
        sh = SHProcessingClient(instance_id="test-instance-id", client=client)
        result = await sh.get_preview(
            bbox=(13.0, 51.0, 14.0, 52.0),
            start_date="2024-01-15",
            end_date="2024-01-15",
            platform="S2",
        )

    assert result == _TINY_PNG
    assert len(result) > 0


@pytest.mark.asyncio
@respx.mock
async def test_get_preview_s1_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """S1 SAR VV preview returns PNG bytes on a 200 response."""
    respx.post(url__startswith=_SH_PROCESS_URL).mock(
        return_value=httpx.Response(200, content=_TINY_PNG, headers={"Content-Type": "image/png"})
    )

    async with httpx.AsyncClient() as client:
        sh = SHProcessingClient(instance_id="test-instance-id", client=client)
        result = await sh.get_preview(
            bbox=(13.0, 51.0, 14.0, 52.0),
            start_date="2024-01-15",
            end_date="2024-01-15",
            platform="S1",
        )

    assert result == _TINY_PNG


@pytest.mark.asyncio
@respx.mock
async def test_get_preview_error_raises() -> None:
    """A non-200 SH response raises SHProcessingError."""
    respx.post(url__startswith=_SH_PROCESS_URL).mock(
        return_value=httpx.Response(403, text="Forbidden")
    )

    async with httpx.AsyncClient() as client:
        sh = SHProcessingClient(instance_id="test-instance-id", client=client)
        with pytest.raises(SHProcessingError, match="403"):
            await sh.get_preview(
                bbox=(13.0, 51.0, 14.0, 52.0),
                start_date="2024-01-15",
                end_date="2024-01-15",
            )


@pytest.mark.asyncio
async def test_missing_instance_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing SH_INSTANCE_ID raises SHProcessingError without a network call.

    We monkeypatch the env var to empty string so the constructor resolves to
    an empty instance_id (the conftest autouse fixture sets it to a test value,
    so we need to override it here).
    """
    monkeypatch.delenv("SH_INSTANCE_ID", raising=False)
    async with httpx.AsyncClient() as client:
        # instance_id=None so the constructor falls back to env (now unset).
        sh = SHProcessingClient(instance_id=None, client=client)
        with pytest.raises(SHProcessingError, match="SH_INSTANCE_ID"):
            await sh.get_preview(
                bbox=(13.0, 51.0, 14.0, 52.0),
                start_date="2024-01-15",
                end_date="2024-01-15",
            )
