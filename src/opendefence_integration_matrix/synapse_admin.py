"""Synapse helper: what the operator does as its bot user"""

import time
from typing import Any, Self
from urllib.parse import quote

import httpx

from .mas_admin import MasAdmin

BOT_LOCALPART = "matrix-operator"
# Create a new bot session this many seconds before the old one expires
BOT_TOKEN_EXPIRY_SKEW = 60.0


class SynapseAdmin:
    """Async wrapper for the Synapse client and admin APIs, as the bot user.

    The bot is a MAS user; its token is a personal session that is created
    again when it expires.  Rooms and users are named by their localparts.
    Use as an async context manager.
    """

    def __init__(self, synapse_url: str, mas: MasAdmin) -> None:
        self._url = synapse_url.rstrip("/")
        self._mas = mas
        self._server_name = ""
        self._token: str | None = None
        self._token_expires: float = 0.0
        self._client: httpx.AsyncClient = httpx.AsyncClient()

    async def __aenter__(self) -> Self:
        resp = await self._client.get(
            f"{self._url}/_matrix/client/v3/account/whoami",
            headers=await self._auth(),
            timeout=10.0,
        )
        resp.raise_for_status()
        self._server_name = str(resp.json()["user_id"]).split(":", maxsplit=1)[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def _auth(self) -> dict[str, str]:
        """Return the bearer header, creating a new bot session when missing or expired."""
        if time.monotonic() >= self._token_expires:
            ulid = await self._mas.ensure_user(BOT_LOCALPART)
            self._token, expires_in = await self._mas.create_bot_token(ulid)
            self._token_expires = time.monotonic() + expires_in - BOT_TOKEN_EXPIRY_SKEW
        return {"Authorization": f"Bearer {self._token}"}

    def _user_id(self, localpart: str) -> str:
        return f"@{localpart}:{self._server_name}"

    async def room_id_for_alias(self, alias: str) -> str | None:
        """Return the room ID for the alias localpart, or None if not found."""
        encoded = quote(f"#{alias}:{self._server_name}", safe="")
        resp = await self._client.get(
            f"{self._url}/_matrix/client/v3/directory/room/{encoded}",
            headers=await self._auth(),
            timeout=10.0,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return str(resp.json()["room_id"])

    async def create_room(self, name: str, alias: str, *, is_space: bool = False, space_id: str | None = None) -> str:
        """Create an invite-only room or space; return its room ID.

        With space_id, the members of that space can join without an invite.
        """
        body: dict[str, Any] = {
            "name": name,
            "room_alias_name": alias,
            "preset": "private_chat",
            "visibility": "private",
        }
        if is_space:
            body["creation_content"] = {"type": "m.space"}
        if space_id:
            join_rules = {"join_rule": "restricted", "allow": [{"type": "m.room_membership", "room_id": space_id}]}
            body["initial_state"] = [{"type": "m.room.join_rules", "content": join_rules}]
        resp = await self._client.post(
            f"{self._url}/_matrix/client/v3/createRoom",
            headers=await self._auth(),
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return str(resp.json()["room_id"])

    async def add_child_to_space(self, space_id: str, room_id: str) -> None:
        """Register the room as a child of the space."""
        resp = await self._client.put(
            f"{self._url}/_matrix/client/v3/rooms/{quote(space_id, safe='')}"
            f"/state/m.space.child/{quote(room_id, safe='')}",
            headers=await self._auth(),
            json={"via": [self._server_name]},
            timeout=10.0,
        )
        resp.raise_for_status()

    async def joined_rooms(self, localpart: str) -> set[str]:
        """Return the IDs of the rooms the user is in."""
        resp = await self._client.get(
            f"{self._url}/_synapse/admin/v1/users/{quote(self._user_id(localpart), safe='')}/joined_rooms",
            headers=await self._auth(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return set(resp.json()["joined_rooms"])

    async def force_join(self, room_id: str, localpart: str) -> None:
        """Join the user to the room without an invite."""
        resp = await self._client.post(
            f"{self._url}/_synapse/admin/v1/join/{quote(room_id, safe='')}",
            headers=await self._auth(),
            json={"user_id": self._user_id(localpart)},
            timeout=10.0,
        )
        resp.raise_for_status()

    async def kick(self, room_id: str, localpart: str) -> None:
        """Remove the user from the room."""
        resp = await self._client.post(
            f"{self._url}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/kick",
            headers=await self._auth(),
            json={"user_id": self._user_id(localpart)},
            timeout=10.0,
        )
        resp.raise_for_status()
