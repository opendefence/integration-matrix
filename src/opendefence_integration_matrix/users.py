"""One UserBinding and one Matrix account, in the space and its rooms, for each User that holds a Matrix role"""

from cloudcoil.apimachinery import ObjectMeta
from cloudcoil.controller import Controller, Request, ResourceKey
from lib_opendefence_core.models.v1alpha1 import (
    API_VERSION,
    SYNCED_CONDITION,
    ObjectRef,
    User,
    UserBinding,
    UserBindingSpec,
)

from .mas_admin import MasAdmin
from .synapse_admin import SynapseAdmin

NAMESPACE = "matrix"
# superadmin is a core role: it counts as matrix-admin here
ADMIN_ROLES = {"matrix-admin", "superadmin"}
MATRIX_ROLES = {"matrix-user", *ADMIN_ROLES}

# TEMPORARY: every account gets this password, for tests before real sign-in exists
TEMPORARY_PASSWORD = "temporary-password"  # nosec B105  # pragma: allowlist secret

# Set by the leader lifespan
mas: MasAdmin | None = None
synapse: SynapseAdmin | None = None
room_ids: dict[str, str] = {}


def role_names(user: User) -> set[str]:
    return {role.name for role in user.status.roles} if user.status else set()


def wanted(user: User) -> bool:
    """Return True for an approved, not revoked User that holds a Matrix role."""
    return (
        user.spec.approved_at is not None
        and user.spec.revoked_at is None
        and not MATRIX_ROLES.isdisjoint(role_names(user))
    )


async def reconcile(request: Request[UserBinding]) -> None:
    """Keep the binding and the account exactly while the User is wanted."""
    if mas is None or synapse is None:
        raise RuntimeError("The MAS and Synapse clients are not set up")
    user = request.cached(User).get(request.name)
    bindings_client = await request.client(UserBinding)
    # The Matrix localpart is the User name: the callsign can change
    if user is None or not wanted(user):
        if request.resource is not None:
            await mas.deactivate_user(request.name)
            await bindings_client.delete(request.name)
        return
    if request.resource is None:
        await bindings_client.create(
            UserBinding(
                api_version=API_VERSION,
                kind="UserBinding",
                metadata=ObjectMeta(name=request.name, namespace=request.namespace),
                spec=UserBindingSpec(user_ref=ObjectRef(name=request.name)),
            )
        )
        return
    try:
        ulid = await mas.ensure_user(request.name)
        await mas.set_password(ulid, TEMPORARY_PASSWORD)
        await synapse.set_displayname(request.name, user.spec.callsign)
        admin = not ADMIN_ROLES.isdisjoint(role_names(user))
        wanted_ids = {room_id for alias, room_id in room_ids.items() if admin or alias != "admin"}
        joined = await synapse.joined_rooms(request.name)
        for room_id in wanted_ids - joined:
            await synapse.force_join(room_id, request.name)
        for room_id in joined & set(room_ids.values()) - wanted_ids:
            await synapse.kick(room_id, request.name)
    except Exception as exc:
        request.condition(SYNCED_CONDITION, False, reason="ProvisionFailed", message=str(exc))
        raise
    request.condition(SYNCED_CONDITION, True, reason="Provisioned")


bindings = Controller(UserBinding, reconcile, name="userbindings", events=False, status_updates=False)


def user_key(user: User) -> list[ResourceKey]:
    return [ResourceKey(user.name, NAMESPACE)] if user.name else []


bindings.watch(User, mapper=user_key)
