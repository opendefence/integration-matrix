"""Unit tests for MasAdmin.

All HTTP calls are intercepted by patching the httpx.AsyncClient method on the
MasAdmin instance, so no real network is required.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from opendefence_integration_matrix.mas_admin import MasAdmin

# httpx.Response needs a request object to call raise_for_status() cleanly.
FAKE_REQUEST = httpx.Request("POST", "http://mas.test/fake")

USER_ULID = "01TESTULID0000000000000000"


def _fake(status: int, body: dict[str, Any]) -> httpx.Response:
    """Build a minimal fake httpx.Response."""
    return httpx.Response(status, json=body, request=FAKE_REQUEST)


def _make_mas(*, with_token: bool = True) -> MasAdmin:
    """Return a MasAdmin ready for unit testing.

    With with_token=True the admin token is pre-seeded so tests can focus on
    the admin API call itself without mocking the token fetch.
    """
    mas = MasAdmin("http://mas.test", "clientid", "clientsecret")
    if with_token:
        mas._token = "mas_admin_token"  # nosec B105
        mas._token_expires = time.monotonic() + 1000
    return mas


# ---------------------------------------------------------------------------
# _ensure_admin_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_token_request_shape() -> None:
    """Token fetch must use basic auth and client_credentials with admin scope."""
    mas = _make_mas(with_token=False)
    with patch.object(mas._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"access_token": "tok", "expires_in": 300})
        token = await mas._ensure_admin_token()
    assert token == "tok"  # nosec B105
    assert mock_post.call_args.kwargs["auth"] == ("clientid", "clientsecret")
    data: dict[str, str] = mock_post.call_args.kwargs["data"]
    assert data["grant_type"] == "client_credentials"
    assert data["scope"] == "urn:mas:admin"


@pytest.mark.asyncio
async def test_admin_token_cached() -> None:
    """A fresh token must be reused, not re-fetched on every call."""
    mas = _make_mas(with_token=False)
    with patch.object(mas._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"access_token": "tok", "expires_in": 300})
        await mas._ensure_admin_token()
        await mas._ensure_admin_token()
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_admin_token_refetched_after_expiry() -> None:
    """A stale token must trigger a new token fetch."""
    mas = _make_mas()
    mas._token_expires = time.monotonic() - 1
    with patch.object(mas._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"access_token": "tok2", "expires_in": 300})
        token = await mas._ensure_admin_token()
    assert token == "tok2"  # nosec B105
    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_user_existing_skips_create() -> None:
    """Existing user: ULID returned from lookup, no create POST."""
    mas = _make_mas()
    client = mas._client
    with (
        patch.object(client, "get", new_callable=AsyncMock) as mock_get,
        patch.object(client, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_get.return_value = _fake(200, {"data": {"id": USER_ULID, "attributes": {"deactivated_at": None}}})
        ulid = await mas.ensure_user("alpha")
    assert ulid == USER_ULID
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_user_reactivates_deactivated() -> None:
    """Deactivated user: reactivate POST hits the user's ULID endpoint, no create."""
    mas = _make_mas()
    client = mas._client
    with (
        patch.object(client, "get", new_callable=AsyncMock) as mock_get,
        patch.object(client, "post", new_callable=AsyncMock) as mock_post,
    ):
        attributes = {"deactivated_at": "2026-11-16T08:00:00Z"}
        mock_get.return_value = _fake(200, {"data": {"id": USER_ULID, "attributes": attributes}})
        mock_post.return_value = _fake(200, {"data": {"id": USER_ULID}})
        ulid = await mas.ensure_user("norppa11")
    assert ulid == USER_ULID
    mock_post.assert_called_once()
    url: str = mock_post.call_args.args[0]
    assert url.endswith(f"/api/admin/v1/users/{USER_ULID}/reactivate")


@pytest.mark.asyncio
async def test_ensure_user_creates_on_404() -> None:
    """Unknown user: 404 lookup must be followed by a create POST."""
    mas = _make_mas()
    client = mas._client
    with (
        patch.object(client, "get", new_callable=AsyncMock) as mock_get,
        patch.object(client, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_get.return_value = _fake(404, {"errors": [{"title": "not found"}]})
        mock_post.return_value = _fake(201, {"data": {"id": USER_ULID}})
        ulid = await mas.ensure_user("alpha")
    assert ulid == USER_ULID
    body: dict[str, Any] = mock_post.call_args.kwargs["json"]
    assert body == {"username": "alpha"}


# ---------------------------------------------------------------------------
# deactivate_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_user_found() -> None:
    """Known user: deactivate POST hits the user's ULID endpoint."""
    mas = _make_mas()
    client = mas._client
    with (
        patch.object(client, "get", new_callable=AsyncMock) as mock_get,
        patch.object(client, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_get.return_value = _fake(200, {"data": {"id": USER_ULID}})
        mock_post.return_value = _fake(200, {"data": {"id": USER_ULID}})
        deactivated = await mas.deactivate_user("norppa11")
    assert deactivated is True
    url: str = mock_post.call_args.args[0]
    assert url.endswith(f"/api/admin/v1/users/{USER_ULID}/deactivate")
    assert mock_post.call_args.kwargs["json"] == {"skip_erase": True}


@pytest.mark.asyncio
async def test_deactivate_user_not_found_skips() -> None:
    """Unknown user (never logged in): no deactivate call, no raise, returns False."""
    mas = _make_mas()
    client = mas._client
    with (
        patch.object(client, "get", new_callable=AsyncMock) as mock_get,
        patch.object(client, "post", new_callable=AsyncMock) as mock_post,
    ):
        mock_get.return_value = _fake(404, {"errors": [{"title": "not found"}]})
        deactivated = await mas.deactivate_user("ghost")  # must not raise
    assert deactivated is False
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# create_bot_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_bot_token() -> None:
    """The session is for the bot user, with the Synapse admin scope."""
    mas = _make_mas()
    with patch.object(mas._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(201, {"data": {"attributes": {"access_token": "tok", "expires_in": 3600}}})
        assert await mas.create_bot_token(USER_ULID) == ("tok", 3600.0)
    url: str = mock_post.call_args.args[0]
    assert url.endswith("/api/admin/v1/personal-sessions")
    body: dict[str, Any] = mock_post.call_args.kwargs["json"]
    assert body["actor_user_id"] == USER_ULID
    assert "urn:synapse:admin:*" in body["scope"]
