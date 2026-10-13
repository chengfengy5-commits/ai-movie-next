"""Administrator authorization backed by the public authentication service."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import AdminStoryboardChatQueriesActor
from .errors import AdminStoryboardChatQueriesForbidden


def authenticate_admin_storyboard_chat_queries_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminStoryboardChatQueriesActor:
    with service.authenticated(authorization, require_membership=False) as principal:
        actor = AdminStoryboardChatQueriesActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
        if not actor.is_superuser:
            raise AdminStoryboardChatQueriesForbidden()
    return actor
