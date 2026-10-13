"""Member identity adapter for media upload routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from haoai_backend.authentication.application import AuthenticationService


@dataclass(frozen=True, slots=True)
class MediaUploadActor:
    user_id: str


MediaUploadIdentityResolver = Callable[
    [str], MediaUploadActor | Awaitable[MediaUploadActor]
]


def create_media_upload_identity_resolver(
    service: AuthenticationService,
) -> MediaUploadIdentityResolver:
    """Copy member identity and close its authentication unit before returning."""

    def resolve_active_member(authorization: str) -> MediaUploadActor:
        with service.authenticated(authorization, require_membership=True) as principal:
            actor = MediaUploadActor(user_id=principal.user.id)
        return actor

    return resolve_active_member
