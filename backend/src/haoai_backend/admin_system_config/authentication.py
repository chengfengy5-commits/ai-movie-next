"""Administrator identity adapter backed by public authentication."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import AdminSystemConfigActor


def authenticate_admin_system_config_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminSystemConfigActor:
    """Copy the local admin actor before the public auth unit closes."""

    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = AdminSystemConfigActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
    return actor
