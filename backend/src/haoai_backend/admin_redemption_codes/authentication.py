"""Administrator authorization through the public authentication boundary."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import AdminRedemptionCodesActor
from .errors import AdminRedemptionCodesForbidden


def authenticate_admin_redemption_codes_actor(
    service: AuthenticationService,
    authorization: str,
) -> AdminRedemptionCodesActor:
    """Copy the local actor while the public authentication context is open."""

    with service.authenticated(
        authorization,
        require_membership=False,
    ) as principal:
        actor = AdminRedemptionCodesActor(
            user_id=principal.user.id,
            is_superuser=bool(principal.user.is_superuser),
        )
    if not actor.is_superuser:
        raise AdminRedemptionCodesForbidden()
    return actor
