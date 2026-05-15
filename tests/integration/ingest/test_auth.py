"""Integration tests for CDSE OAuth2 auth.

All HTTP interactions are mocked with respx (httpx-native mock transport).
No live network calls are made.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from services.ingest.src.auth import CDSEAuth, CDSETokenError

_TOKEN_ENDPOINT = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)

_FAKE_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIn0.fake.signature"
_TOKEN_RESPONSE = {
    "access_token": _FAKE_TOKEN,
    "expires_in": 299,
    "token_type": "Bearer",
    "scope": "openid profile email",
}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_token_success() -> None:
    """Token endpoint returns 200 → access_token is stored and returned."""
    respx.post(_TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_TOKEN_RESPONSE))

    auth = CDSEAuth(client_id="test-id", client_secret="test-secret")
    async with httpx.AsyncClient() as client:
        token = await auth.fetch_token(client)

    assert token == _FAKE_TOKEN
    assert auth._access_token == _FAKE_TOKEN
    assert auth._expires_at > 0


@pytest.mark.asyncio
@respx.mock
async def test_get_token_reuses_cached() -> None:
    """get_token() should NOT call the endpoint a second time when token is valid."""
    respx.post(_TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_TOKEN_RESPONSE))

    auth = CDSEAuth(client_id="test-id", client_secret="test-secret")
    async with httpx.AsyncClient() as client:
        token1 = await auth.get_token(client)
        token2 = await auth.get_token(client)  # should reuse cached token

    assert token1 == token2
    # Endpoint was called exactly once.
    assert respx.calls.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_auth_headers_format() -> None:
    """auth_headers() returns a properly formatted Bearer header."""
    respx.post(_TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_TOKEN_RESPONSE))

    auth = CDSEAuth(client_id="test-id", client_secret="test-secret")
    async with httpx.AsyncClient() as client:
        headers = await auth.auth_headers(client)

    assert headers == {"Authorization": f"Bearer {_FAKE_TOKEN}"}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_token_error_raises() -> None:
    """A non-200 response raises CDSETokenError with the status code."""
    respx.post(_TOKEN_ENDPOINT).mock(return_value=httpx.Response(401, text="Unauthorized"))

    auth = CDSEAuth(client_id="bad-id", client_secret="bad-secret")
    with pytest.raises(CDSETokenError, match="401"):
        async with httpx.AsyncClient() as client:
            await auth.fetch_token(client)


@pytest.mark.asyncio
async def test_missing_credentials_raises() -> None:
    """Missing client_id / client_secret raises CDSETokenError without a network call."""
    # No env vars set (conftest.py clears CDSE_CLIENT_ID / CDSE_CLIENT_SECRET).
    auth = CDSEAuth(client_id="", client_secret="")
    with pytest.raises(CDSETokenError, match="CDSE_CLIENT_ID"):
        await auth.fetch_token()
