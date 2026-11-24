"""The operator: the UserBinding controller, and the MAS and Synapse clients of the elected leader"""

import logging
import os
from collections.abc import AsyncIterator

from cloudcoil.application import Application, RBACRule
from cloudcoil.controller import HealthServer
from lib_opendefence_core.models.v1alpha1 import UserBinding

from . import rooms, users
from .mas_admin import MasAdmin
from .synapse_admin import SynapseAdmin

NAME = "matrix-operator"
HEALTH_PORT = 8080

app = Application(
    NAME,
    users.bindings,
    namespace=users.NAMESPACE,
    leader_election=True,
    health=HealthServer(host="0.0.0.0", port=HEALTH_PORT),  # nosec B104
    rules=(RBACRule(UserBinding, verbs=("create", "delete")),),
)


@app.lifespan(scope="leader")
async def matrix() -> AsyncIterator[None]:
    async with (
        MasAdmin(
            "http://matrix-matrix-authentication-service:8080",
            os.environ["MAS_ADMIN_CLIENT_ID"],
            os.environ["MAS_ADMIN_CLIENT_SECRET"],
        ) as mas,
        SynapseAdmin("http://matrix-synapse:8008", mas) as synapse,
    ):
        users.mas = mas
        users.synapse = synapse
        users.room_ids = await rooms.ensure_rooms(synapse)
        yield


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # httpx logs one line for each request, the lease renewals included
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.main()
