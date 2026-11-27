from unittest.mock import AsyncMock, call

from opendefence_integration_matrix import rooms
from opendefence_integration_matrix.synapse_admin import SynapseAdmin


def _room_id(_name: str, alias: str, *, is_space: bool = False, space_id: str | None = None) -> str:
    return f"!{alias}"


def _synapse() -> AsyncMock:
    synapse = AsyncMock(spec=SynapseAdmin)
    synapse.server_name = "kotka.example.test"
    return synapse


async def test_ensure_rooms_creates_the_missing() -> None:
    """An existing room is kept, a missing one is created, and each room is put in the space."""
    synapse = _synapse()
    synapse.room_id_for_alias.side_effect = {"space": "!space"}.get
    synapse.create_room.side_effect = _room_id

    ids = await rooms.ensure_rooms(synapse)

    assert ids == {
        "space": "!space",
        "general": "!general",
        "helpdesk": "!helpdesk",
        "offtopic": "!offtopic",
    }
    assert synapse.create_room.await_args_list == [
        call("General", "general", space_id="!space"),
        call("Helpdesk", "helpdesk", space_id="!space"),
        call("Offtopic", "offtopic", space_id="!space"),
    ]
    assert synapse.add_child_to_space.await_args_list == [
        call("!space", "!general"),
        call("!space", "!helpdesk"),
        call("!space", "!offtopic"),
    ]


async def test_space_is_named_after_the_deployment() -> None:
    synapse = _synapse()
    synapse.room_id_for_alias.return_value = None
    synapse.create_room.return_value = "!new"

    await rooms.ensure_rooms(synapse)

    assert synapse.create_room.await_args_list[0] == call("kotka", "space", is_space=True)
