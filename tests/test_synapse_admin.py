"""Unit tests for SynapseAdmin.

All HTTP calls are intercepted by patching the httpx.AsyncClient method on the
SynapseAdmin instance, so no real network is required.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from opendefence_integration_matrix.synapse_admin import BOT_LOCALPART, SynapseAdmin

# httpx.Response needs a request object to call raise_for_status() cleanly.
FAKE_REQUEST = httpx.Request("POST", "http://synapse.test/fake")

ROOM = "!room"
ROOMS_URL = "http://synapse.test/_matrix/client/v3/rooms"
ADMIN_URL = "http://synapse.test/_synapse/admin/v1"


def _fake(status: int, body: dict[str, Any]) -> httpx.Response:
    """Build a minimal fake httpx.Response."""
    return httpx.Response(status, json=body, request=FAKE_REQUEST)


def _make_synapse(*, with_token: bool = True) -> SynapseAdmin:
    """Return a SynapseAdmin with a mocked MAS client and, by default, a bot token."""
    mas = AsyncMock()
    mas.ensure_user.return_value = "01BOT"
    mas.create_bot_token.return_value = ("bot-token", 3600.0)
    sa = SynapseAdmin("http://synapse.test", mas)
    sa.server_name = "example.test"
    if with_token:
        sa._token = "test-token"  # nosec B105
        sa._token_expires = time.monotonic() + 1000
    return sa


# ---------------------------------------------------------------------------
# Bot session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_creates_bot_session() -> None:
    """Without a token the bot user is ensured and a session is created for it."""
    sa = _make_synapse(with_token=False)
    assert await sa._auth() == {"Authorization": "Bearer bot-token"}
    sa._mas.ensure_user.assert_awaited_once_with(BOT_LOCALPART)  # type: ignore[attr-defined]
    sa._mas.create_bot_token.assert_awaited_once_with("01BOT")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_auth_reuses_token() -> None:
    """A fresh token must be reused, not created again on every call."""
    sa = _make_synapse(with_token=False)
    await sa._auth()
    await sa._auth()
    sa._mas.create_bot_token.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_auth_new_session_after_expiry() -> None:
    """An expired token must be replaced with a new session."""
    sa = _make_synapse()
    sa._token_expires = time.monotonic() - 1
    assert await sa._auth() == {"Authorization": "Bearer bot-token"}


@pytest.mark.asyncio
async def test_enter_reads_server_name() -> None:
    """The server name comes from the bot's own user ID, port included."""
    sa = _make_synapse()
    with patch.object(sa._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _fake(200, {"user_id": "@matrix-operator:other.test:8448"})
        async with sa:
            assert sa.server_name == "other.test:8448"


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_id_for_alias_found() -> None:
    """The alias is built from the localpart and URL-encoded."""
    sa = _make_synapse()
    with patch.object(sa._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _fake(200, {"room_id": ROOM})
        assert await sa.room_id_for_alias("general") == ROOM
    url: str = mock_get.call_args.args[0]
    assert url.endswith("/directory/room/%23general%3Aexample.test")


@pytest.mark.asyncio
async def test_room_id_for_alias_not_found() -> None:
    """404 means the room does not exist yet: None, no raise."""
    sa = _make_synapse()
    with patch.object(sa._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _fake(404, {"errcode": "M_NOT_FOUND"})
        assert await sa.room_id_for_alias("general") is None


@pytest.mark.asyncio
async def test_create_room_is_invite_only() -> None:
    """Without a space the room is invite-only and not listed in the room directory."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"room_id": ROOM})
        assert await sa.create_room("Admin", "admin") == ROOM
    assert mock_post.call_args.kwargs["json"] == {
        "name": "Admin",
        "room_alias_name": "admin",
        "preset": "private_chat",
        "visibility": "private",
    }


@pytest.mark.asyncio
async def test_create_room_for_the_members_of_a_space() -> None:
    """With a space the join rule is restricted to the members of that space."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"room_id": ROOM})
        await sa.create_room("General", "general", space_id="!space")
    body: dict[str, Any] = mock_post.call_args.kwargs["json"]
    assert body["initial_state"] == [
        {
            "type": "m.room.join_rules",
            "content": {"join_rule": "restricted", "allow": [{"type": "m.room_membership", "room_id": "!space"}]},
        }
    ]


@pytest.mark.asyncio
async def test_create_space() -> None:
    """is_space sets the room type."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"room_id": ROOM})
        await sa.create_room("Space", "space", is_space=True)
    body: dict[str, Any] = mock_post.call_args.kwargs["json"]
    assert body["creation_content"] == {"type": "m.space"}
    assert "initial_state" not in body


@pytest.mark.asyncio
async def test_create_room_failure_raises() -> None:
    """A failed createRoom must raise so the caller retries."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(400, {"errcode": "M_ROOM_IN_USE"})
        with pytest.raises(httpx.HTTPStatusError):
            await sa.create_room("General", "general")


@pytest.mark.asyncio
async def test_add_child_to_space() -> None:
    """The child is a state event of the space, keyed by the room ID."""
    sa = _make_synapse()
    with patch.object(sa._client, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = _fake(200, {"event_id": "$e"})
        await sa.add_child_to_space("!space", ROOM)
    assert mock_put.call_args.args[0] == f"{ROOMS_URL}/%21space/state/m.space.child/%21room"
    assert mock_put.call_args.kwargs["json"] == {"via": ["example.test"]}


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_joined_rooms() -> None:
    """The user's rooms come back as a set of room IDs."""
    sa = _make_synapse()
    with patch.object(sa._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _fake(200, {"joined_rooms": [ROOM, "!other"], "total": 2})
        assert await sa.joined_rooms("norppa11") == {ROOM, "!other"}
    assert mock_get.call_args.args[0] == f"{ADMIN_URL}/users/%40norppa11%3Aexample.test/joined_rooms"


@pytest.mark.asyncio
async def test_force_join() -> None:
    """The user is named by full user ID in the body."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {"room_id": ROOM})
        await sa.force_join(ROOM, "norppa11")
    assert mock_post.call_args.args[0] == f"{ADMIN_URL}/join/%21room"
    assert mock_post.call_args.kwargs["json"] == {"user_id": "@norppa11:example.test"}


@pytest.mark.asyncio
async def test_force_join_unknown_user_raises() -> None:
    """404 means MAS has not provisioned the user yet: raise so the caller retries."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(404, {"errcode": "M_NOT_FOUND"})
        with pytest.raises(httpx.HTTPStatusError):
            await sa.force_join(ROOM, "ghost")


@pytest.mark.asyncio
async def test_kick() -> None:
    """The kick is a client API call by the bot."""
    sa = _make_synapse()
    with patch.object(sa._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _fake(200, {})
        await sa.kick(ROOM, "norppa11")
    assert mock_post.call_args.args[0] == f"{ROOMS_URL}/%21room/kick"
    assert mock_post.call_args.kwargs["json"] == {"user_id": "@norppa11:example.test"}
