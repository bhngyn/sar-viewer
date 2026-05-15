"""CDSE OAuth2 client-credentials flow with token caching and automatic refresh.

Why client credentials (not auth-code):
  - This is a non-interactive server-side tool. There's no browser; the
    investigator provides CDSE_CLIENT_ID + CDSE_CLIENT_SECRET in .env.
  - Token endpoint: https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token

OPSEC note: credentials are read from environment only — never hardcoded,
never logged (structlog redacts them via _safe_env helper below).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_TOKEN_ENDPOINT = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)

# Refresh the token this many seconds before it actually expires to avoid
# races between check-time and use-time.
_EXPIRY_BUFFER_SECONDS = 30


class CDSETokenError(Exception):
    """Raised when the CDSE token endpoint returns an error."""


class CDSEAuth:
    """Thin bearer-token manager for CDSE client-credentials flow.

    Typical usage::

        auth = CDSEAuth()
        headers = await auth.auth_headers()

    The instance caches the token in memory and silently refreshes when
    it has fewer than ``_EXPIRY_BUFFER_SECONDS`` seconds left.

    Thread/task safety: this class is NOT thread-safe; each async task
    should use its own instance or protect shared access with a lock.
    For Phase 1's single-worker design this is fine.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        token_endpoint: str = _TOKEN_ENDPOINT,
    ) -> None:
        # Prefer explicit args (useful in tests) over env vars.
        self._client_id = client_id or os.environ.get("CDSE_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("CDSE_CLIENT_SECRET", "")
        self._token_endpoint = token_endpoint

        self._access_token: str | None = None
        self._expires_at: float = 0.0  # epoch seconds

    def _is_valid(self) -> bool:
        """True if the cached token has not expired (with buffer)."""
        return (
            self._access_token is not None
            and time.monotonic() < self._expires_at - _EXPIRY_BUFFER_SECONDS
        )

    async def fetch_token(self, client: httpx.AsyncClient | None = None) -> str:
        """Request a new access token from the CDSE token endpoint.

        Uses the ``client`` if provided; otherwise creates a temporary one.
        The temporary client inherits proxy settings from the environment
        (httpx respects HTTPS_PROXY / ALL_PROXY by default — trust_env is True).
        """
        if not self._client_id or not self._client_secret:
            raise CDSETokenError(
                "CDSE_CLIENT_ID and CDSE_CLIENT_SECRET must be set in the environment."
            )

        payload = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }

        log.info("cdse.token.fetch", endpoint=self._token_endpoint)

        _close_after = client is None
        _client = client or httpx.AsyncClient()
        try:
            response = await _client.post(self._token_endpoint, data=payload)
        finally:
            if _close_after:
                await _client.aclose()

        if response.status_code != 200:
            raise CDSETokenError(
                f"CDSE token endpoint returned {response.status_code}: {response.text}"
            )

        data: dict[str, Any] = response.json()
        token: str = data["access_token"]
        expires_in: int = int(data.get("expires_in", 300))

        self._access_token = token
        self._expires_at = time.monotonic() + expires_in

        log.info("cdse.token.acquired", expires_in=expires_in)
        return token

    async def get_token(self, client: httpx.AsyncClient | None = None) -> str:
        """Return a valid access token, fetching a new one if needed."""
        if not self._is_valid():
            await self.fetch_token(client)
        assert self._access_token is not None  # fetch_token guarantees this
        return self._access_token

    async def auth_headers(self, client: httpx.AsyncClient | None = None) -> dict[str, str]:
        """Return Authorization headers ready to attach to any CDSE API request."""
        token = await self.get_token(client)
        return {"Authorization": f"Bearer {token}"}
