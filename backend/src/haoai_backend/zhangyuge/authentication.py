"""Administrator identity adapter backed by public authentication."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import ZhangyugeAdminActor


def authenticate_zhangyuge_admin_actor(
    service: AuthenticationService,
    authorization: str,
) -> ZhangyugeAdminActor:
    """Copy the administrator identity before its authentication unit closes."""

    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = ZhangyugeAdminActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
    return actor
