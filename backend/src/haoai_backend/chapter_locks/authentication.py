"""Trusted chapter lock identity resolution through public authentication."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .domain import ChapterLockActor


class AuthenticatedUser(Protocol):
    id: str
    username: str


class AuthenticatedPrincipal(Protocol):
    user: AuthenticatedUser


class PublicAuthenticationService(Protocol):
    def authenticated(
        self,
        authorization: str,
        *,
        require_membership: bool = False,
    ) -> AbstractContextManager[AuthenticatedPrincipal]: ...


def resolve_chapter_lock_actor(
    service: PublicAuthenticationService,
    authorization: str,
) -> ChapterLockActor:
    """Copy the authenticated username while the public auth context is active."""
    with service.authenticated(
        authorization,
        require_membership=True,
    ) as principal:
        return ChapterLockActor(
            user_id=principal.user.id,
            username=principal.user.username,
        )
