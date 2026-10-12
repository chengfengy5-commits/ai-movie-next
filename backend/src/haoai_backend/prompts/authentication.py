"""Prompt-local adapters for authenticated membership and administrator access."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import PromptActor


@dataclass(frozen=True, slots=True)
class PromptIdentityResolvers:
    active: Callable[[str], PromptActor]
    administrator: Callable[[str], PromptActor]


def create_prompt_identity_resolvers(
    service: AuthenticationService,
) -> PromptIdentityResolvers:
    """Resolve only module-local actors and close auth state before returning them."""

    def active(authorization: str) -> PromptActor:
        with service.authenticated(authorization, require_membership=True) as principal:
            actor = PromptActor(
                user_id=principal.user.id,
                is_superuser=principal.user.is_superuser,
            )
        return actor

    def administrator(authorization: str) -> PromptActor:
        with service.authenticated(authorization, require_membership=False) as principal:
            if not principal.user.is_superuser:
                raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
            actor = PromptActor(
                user_id=principal.user.id,
                is_superuser=principal.user.is_superuser,
            )
        return actor

    return PromptIdentityResolvers(active=active, administrator=administrator)
