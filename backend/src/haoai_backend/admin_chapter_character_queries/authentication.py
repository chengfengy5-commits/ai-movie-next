"""Administrator identity adapter backed by public authentication."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService
from haoai_backend.authentication.errors import AuthenticationError

from .domain import AdminChapterCharacterActor


def authenticate_admin_chapter_character_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminChapterCharacterActor:
    """Copy the admin actor before the public authentication UOW is closed."""
    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = AdminChapterCharacterActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AuthenticationError(403, "权限不足，仅管理员可执行此操作")
    return actor
