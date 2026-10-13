"""Member authentication adapter for the eight AIStarsLab routes."""

from __future__ import annotations

from dataclasses import dataclass

from haoai_backend.authentication.application import AuthenticationService


@dataclass(frozen=True, slots=True)
class AistarslabActor:
    user_id: str
    is_superuser: bool


def authenticate_aistarslab_actor(
    service: AuthenticationService,
    authorization: str,
) -> AistarslabActor:
    """Copy the member facts needed by this feature before auth UOW closes."""
    with service.authenticated(
        authorization,
        require_membership=True,
    ) as principal:
        actor = AistarslabActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
    return actor
