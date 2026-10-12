"""Profile-specific trusted actor copied from the public authentication boundary."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import UserProfileActor


def authenticate_user_profile_actor(
    service: AuthenticationService,
    authorization: str,
) -> UserProfileActor:
    """Copy the authenticated member snapshot before its auth unit closes."""
    with service.authenticated(
        authorization,
        require_membership=True,
    ) as principal:
        actor = UserProfileActor(
            user_id=principal.user.id,
            username=principal.user.username,
            email=principal.user.email,
            bio=principal.user.bio,
        )
    return actor
