"""Narrow public identity resolvers for task-family authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from haoai_backend.shared.identity import TrustedActor

from .application import AuthenticationService
from .configuration import AuthenticationRuntime
from .errors import AuthenticationError
from .ports import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class TaskIdentityResolvers:
    """Trusted identities resolved only after the auth unit has closed."""

    active: Callable[[str], TrustedActor]
    admin: Callable[[str], TrustedActor]


def create_task_identity_resolvers(
    runtime: AuthenticationRuntime | None,
    *,
    unit_of_work_factory: UnitOfWorkFactory | None,
) -> TaskIdentityResolvers:
    """Return inert active/admin closures backed by the existing auth service."""

    def active(authorization: str) -> TrustedActor:
        service = AuthenticationService(runtime, unit_of_work_factory)
        with service.authenticated(authorization, require_membership=True) as principal:
            actor = TrustedActor(user_id=principal.user.id)
        return actor

    def admin(authorization: str) -> TrustedActor:
        service = AuthenticationService(runtime, unit_of_work_factory)
        with service.authenticated(authorization, require_membership=False) as principal:
            if not principal.user.is_superuser:
                raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
            actor = TrustedActor(user_id=principal.user.id)
        return actor

    return TaskIdentityResolvers(active=active, admin=admin)
