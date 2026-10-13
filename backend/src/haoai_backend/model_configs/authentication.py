"""Module-local actor adapters backed by the public authentication boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import ModelConfigActor


@dataclass(frozen=True, slots=True)
class ModelConfigIdentityResolvers:
    active: Callable[[str], ModelConfigActor]
    administrator: Callable[[str], ModelConfigActor]


def create_model_config_identity_resolvers(
    service: AuthenticationService,
) -> ModelConfigIdentityResolvers:
    """Copy actor facts and close the authentication unit before returning."""

    def active(authorization: str) -> ModelConfigActor:
        with service.authenticated(authorization, require_membership=True) as principal:
            actor = ModelConfigActor(
                user_id=principal.user.id,
                is_superuser=bool(principal.user.is_superuser),
            )
        return actor

    def administrator(authorization: str) -> ModelConfigActor:
        with service.authenticated(authorization, require_membership=False) as principal:
            actor = ModelConfigActor(
                user_id=principal.user.id,
                is_superuser=bool(principal.user.is_superuser),
            )
        if not actor.is_superuser:
            raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
        return actor

    return ModelConfigIdentityResolvers(active=active, administrator=administrator)
