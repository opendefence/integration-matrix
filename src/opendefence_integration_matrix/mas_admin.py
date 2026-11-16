"""MAS (Matrix Authentication Service) admin API helper"""

import logging
import time
from typing import Any, Self
from urllib.parse import quote

import httpx

LOGGER = logging.getLogger(__name__)

ADMIN_SCOPE = "urn:mas:admin"
# Re-fetch the client-credentials token this many seconds before it expires
TOKEN_EXPIRY_SKEW = 30.0


class MasAdmin:
    """Async wrapper for the MAS admin API.

    Authenticates with OAuth2 client credentials (``urn:mas:admin`` scope);
    the short-lived admin token is cached and refreshed on demand.
    Use as an async context manager.
    """

    def __init__(self, mas_url: str, client_id: str, client_secret: str) -> None:
        self._url = mas_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._client: httpx.AsyncClient = httpx.AsyncClient()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _ensure_admin_token(self) -> str:
        """Return a cached admin token, fetching a fresh one when missing or stale."""
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        resp = await self._client.post(
            f"{self._url}/oauth2/token",
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials", "scope": ADMIN_SCOPE},
            timeout=10.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = str(payload["access_token"])
        self._token_expires = time.monotonic() + float(payload.get("expires_in", 300)) - TOKEN_EXPIRY_SKEW
        return self._token

    async def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._ensure_admin_token()}"}

    async def _user(self, localpart: str) -> dict[str, Any] | None:
        """Return the MAS user for localpart, or None if not found."""
        encoded = quote(localpart, safe="")
        resp = await self._client.get(
            f"{self._url}/api/admin/v1/users/by-username/{encoded}",
            headers=await self._auth(),
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return dict(resp.json()["data"])

    async def ensure_user(self, localpart: str) -> str:
        """Return the user's ULID, creating or reactivating the user if needed."""
        existing = await self._user(localpart)
        if existing:
            ulid = str(existing["id"])
            if existing["attributes"].get("deactivated_at"):
                resp = await self._client.post(
                    f"{self._url}/api/admin/v1/users/{ulid}/reactivate",
                    headers=await self._auth(),
                    timeout=30.0,
                )
                resp.raise_for_status()
                LOGGER.info("Reactivated MAS user %s (%s)", localpart, ulid)
            return ulid
        resp = await self._client.post(
            f"{self._url}/api/admin/v1/users",
            headers=await self._auth(),
            json={"username": localpart},
            timeout=30.0,
        )
        resp.raise_for_status()
        ulid = str(resp.json()["data"]["id"])
        LOGGER.info("Created MAS user %s (%s)", localpart, ulid)
        return ulid

    async def set_password(self, ulid: str, password: str) -> None:
        """Set the user's password, without the complexity check."""
        resp = await self._client.post(
            f"{self._url}/api/admin/v1/users/{ulid}/set-password",
            headers=await self._auth(),
            json={"password": password, "skip_password_check": True},
            timeout=30.0,
        )
        resp.raise_for_status()

    async def deactivate_user(self, localpart: str) -> bool:
        """Deactivate user in MAS, without erasure so ensure_user can reactivate them.

        Returns True if the user was deactivated, False if they do not exist
        in MAS.  Raises httpx.HTTPError on real failures.
        """
        user = await self._user(localpart)
        if user is None:
            return False
        ulid = str(user["id"])
        resp = await self._client.post(
            f"{self._url}/api/admin/v1/users/{ulid}/deactivate",
            headers=await self._auth(),
            json={"skip_erase": True},
            timeout=30.0,
        )
        resp.raise_for_status()
        LOGGER.info("Deactivated MAS user %s (%s)", localpart, ulid)
        return True
