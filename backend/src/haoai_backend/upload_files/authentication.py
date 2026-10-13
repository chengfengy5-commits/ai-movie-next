"""Upload-file identity adapter for the shared authentication service."""

from __future__ import annotations

from collections.abc import Callable

from haoai_backend.authentication.application import AuthenticationService

from .domain import UploadFileActor

UploadFileIdentityResolver = Callable[[str], UploadFileActor]


def create_upload_file_identity_resolver(
    service: AuthenticationService,
) -> UploadFileIdentityResolver:
    """Return an inert resolver that closes authentication before returning an actor."""

    def resolve_active_member(authorization: str) -> UploadFileActor:
        with service.authenticated(authorization, require_membership=True) as principal:
            actor = UploadFileActor(user_id=principal.user.id)
        return actor

    return resolve_active_member
