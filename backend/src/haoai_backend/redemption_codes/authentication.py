"""Authentication adapters that return pure actor snapshots."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import RedemptionCodesActor
from .errors import RedemptionCodesForbidden


def authenticate_redemption_actor(
    service: AuthenticationService,
    authorization: str,
) -> RedemptionCodesActor:
    """Copy identity and membership facts before the auth UOW is closed."""

    with service.authenticated(authorization, require_membership=False) as principal:
        user = principal.user
        actor = RedemptionCodesActor(
            user_id=user.id,
            is_superuser=bool(user.is_superuser),
            membership_type=user.membership_type,
            membership_expires_at=user.membership_expires_at,
        )
    return actor


def authenticate_redemption_administrator(
    service: AuthenticationService,
    authorization: str,
) -> RedemptionCodesActor:
    actor = authenticate_redemption_actor(service, authorization)
    if not actor.is_superuser:
        raise RedemptionCodesForbidden()
    return actor
