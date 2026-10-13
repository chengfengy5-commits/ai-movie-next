"""Administrator actor adapter backed by the public authentication service."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import AdminPromptConfigsActor


_ADMIN_FORBIDDEN_DETAIL = "权限不足，仅管理员可执行此操作"


def authenticate_admin_prompt_configs_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminPromptConfigsActor:
    """Copy account-only admin facts before the authentication context closes."""

    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = AdminPromptConfigsActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(403, _ADMIN_FORBIDDEN_DETAIL)
    return actor
