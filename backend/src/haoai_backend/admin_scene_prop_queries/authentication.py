"""Module-local administrator authorization through public authentication."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import AdminScenePropQueriesActor


def authenticate_admin_scene_prop_queries_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminScenePropQueriesActor:
    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        user = principal.user
        actor = AdminScenePropQueriesActor(
            user_id=user.id,
            is_superuser=bool(user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(
                403,
                "权限不足，仅管理员可执行此操作",
            )
    return actor
