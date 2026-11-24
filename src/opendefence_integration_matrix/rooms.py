"""The space and the rooms every Matrix user is in"""

from .synapse_admin import SynapseAdmin

# Alias localpart and name of each room in the space
ROOMS = {"general": "General", "helpdesk": "Helpdesk", "offtopic": "Offtopic"}


async def ensure_rooms(synapse: SynapseAdmin) -> dict[str, str]:
    """Create the space and its rooms where missing; return the room IDs by alias localpart."""
    # The space is named after the deployment: the first label of the server name
    deployment = synapse.server_name.split(".", maxsplit=1)[0]
    space_id = await synapse.room_id_for_alias("space") or await synapse.create_room(deployment, "space", is_space=True)
    ids = {"space": space_id}
    for alias, name in ROOMS.items():
        ids[alias] = await synapse.room_id_for_alias(alias) or await synapse.create_room(name, alias, space_id=space_id)
        await synapse.add_child_to_space(space_id, ids[alias])
    return ids
