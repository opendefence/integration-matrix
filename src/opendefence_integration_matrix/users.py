"""One UserBinding and one Matrix account for each User that holds a Matrix role"""

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

NAMESPACE = "matrix"
# superadmin is a core role: it counts as matrix-admin here
MATRIX_ROLES = {"matrix-user", "matrix-admin", "superadmin"}

# Set by the leader lifespan
mas: MasAdmin | None = None


def wanted(user: User) -> bool:
    """Return True for an approved, not revoked User that holds a Matrix role."""
    roles = user.status.roles if user.status else []
    return (
        user.spec.approved_at is not None
        and user.spec.revoked_at is None
        and not MATRIX_ROLES.isdisjoint(role.name for role in roles)
    )


async def reconcile(request: Request[UserBinding]) -> None:
    """Keep the binding and the account exactly while the User is wanted."""
    if mas is None:
        raise RuntimeError("The MAS client is not set up")
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
        await mas.ensure_user(request.name)
    except Exception as exc:
        request.condition(SYNCED_CONDITION, False, reason="ProvisionFailed", message=str(exc))
        raise
    request.condition(SYNCED_CONDITION, True, reason="Provisioned")


bindings = Controller(UserBinding, reconcile, name="userbindings", events=False, status_updates=False)


def user_key(user: User) -> list[ResourceKey]:
    return [ResourceKey(user.name, NAMESPACE)] if user.name else []


bindings.watch(User, mapper=user_key)
