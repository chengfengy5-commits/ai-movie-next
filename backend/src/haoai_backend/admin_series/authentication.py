"""Administrator identity extraction through public authentication only."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import AdminSeriesActor
from .errors import AdminSeriesForbidden


def authenticate_admin_series_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminSeriesActor:
    """Copy the account identity before closing public authentication state."""
    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = AdminSeriesActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
    if not actor.is_superuser:
        raise AdminSeriesForbidden()
    return actor
