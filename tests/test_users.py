from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from cloudcoil.apimachinery import ObjectMeta
from cloudcoil.controller import Request
from lib_opendefence_core.models.v1alpha1 import (
    API_VERSION,
    ObjectRef,
    ResolvedRef,
    User,
    UserBinding,
    UserBindingSpec,
    UserSpec,
    UserStatus,
)

from opendefence_integration_matrix import users
from opendefence_integration_matrix.mas_admin import MasAdmin
from opendefence_integration_matrix.synapse_admin import SynapseAdmin

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BINDING = UserBinding(
    api_version=API_VERSION,
    kind="UserBinding",
    metadata=ObjectMeta(name="user-alpha", namespace="matrix"),
    spec=UserBindingSpec(user_ref=ObjectRef(name="user-alpha")),
)


def user(*, roles: tuple[str, ...] = ("matrix-user",), approved: bool = True, revoked: bool = False) -> User:
    return User(
        api_version=API_VERSION,
        kind="User",
        metadata=ObjectMeta(name="user-alpha"),
        spec=UserSpec(
            callsign="Lokki",
            approved_at=NOW if approved else None,
            revoked_at=NOW if revoked else None,
        ),
        status=UserStatus(roles=[ResolvedRef(name=name, uid=name) for name in roles]),
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Record the MAS, Synapse and UserBinding client calls in order."""
    recorded = Mock()
    recorded.mas = AsyncMock(spec=MasAdmin)
    recorded.bindings = AsyncMock()
    recorded.synapse = AsyncMock(spec=SynapseAdmin)
    recorded.synapse.joined_rooms.return_value = {"!general"}
    monkeypatch.setattr(users, "mas", recorded.mas)
    monkeypatch.setattr(users, "synapse", recorded.synapse)
    monkeypatch.setattr(users, "room_ids", {"space": "!space", "general": "!general"})
    return recorded


def request_for(calls: Mock, found: User | None, binding: UserBinding | None) -> Request[UserBinding]:
    request = Mock(spec=Request)
    request.name = "user-alpha"
    request.namespace = "matrix"
    request.resource = binding
    request.cached.return_value.get.return_value = found
    request.client = AsyncMock(return_value=calls.bindings)
    calls.attach_mock(request.condition, "condition")
    return cast(Request[UserBinding], request)


def names(calls: Mock) -> list[str]:
    return [call[0] for call in calls.mock_calls]


async def test_wanted_user_without_a_binding_gets_one(calls: Mock) -> None:
    await users.reconcile(request_for(calls, user(), None))
    assert names(calls) == ["bindings.create"]
    created: UserBinding = calls.bindings.create.await_args.args[0]
    assert (created.name, created.namespace, created.spec.user_ref.name) == ("user-alpha", "matrix", "user-alpha")


@pytest.mark.parametrize("role", ["matrix-user", "matrix-admin", "superadmin"])
async def test_wanted_user_with_a_binding_is_provisioned(calls: Mock, role: str) -> None:
    await users.reconcile(request_for(calls, user(roles=(role,)), BINDING))
    calls.mas.ensure_user.assert_awaited_once_with("user-alpha")
    calls.mas.set_password.assert_awaited_once_with(calls.mas.ensure_user.return_value, users.TEMPORARY_PASSWORD)
    calls.condition.assert_called_once_with("Synced", True, reason="Provisioned")


async def test_provisioned_user_is_named_and_joined_to_the_missing_rooms(calls: Mock) -> None:
    await users.reconcile(request_for(calls, user(), BINDING))
    calls.synapse.set_displayname.assert_awaited_once_with("user-alpha", "Lokki")
    calls.synapse.force_join.assert_awaited_once_with("!space", "user-alpha")


async def test_failed_provision_is_reported_and_raised(calls: Mock) -> None:
    calls.mas.ensure_user.side_effect = RuntimeError("MAS is down")
    with pytest.raises(RuntimeError):
        await users.reconcile(request_for(calls, user(), BINDING))
    calls.condition.assert_called_once_with("Synced", False, reason="ProvisionFailed", message="MAS is down")


@pytest.mark.parametrize("found", [user(roles=("tak-user",)), user(approved=False), user(revoked=True), None])
async def test_unwanted_user_is_deactivated_before_the_binding_goes(calls: Mock, found: User | None) -> None:
    await users.reconcile(request_for(calls, found, BINDING))
    assert names(calls) == ["mas.deactivate_user", "bindings.delete"]
    calls.mas.deactivate_user.assert_awaited_once_with("user-alpha")
    calls.bindings.delete.assert_awaited_once_with("user-alpha")


@pytest.mark.parametrize("found", [user(roles=()), None])
async def test_unwanted_user_without_a_binding_is_left_alone(calls: Mock, found: User | None) -> None:
    await users.reconcile(request_for(calls, found, None))
    assert names(calls) == []


def test_user_key() -> None:
    assert users.user_key(user()) == [users.ResourceKey("user-alpha", "matrix")]
