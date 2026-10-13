"""Module-local administrator authorization using public authentication contracts."""

from __future__ import annotations

from haoai_backend.authentication.application import AuthenticationService

from .domain import AdminPurchasePackagesActor
from .errors import PurchasePackageAdminForbidden


def resolve_admin_actor(
    authentication_service: AuthenticationService, authorization: str
) -> AdminPurchasePackagesActor:
    with authentication_service.authenticated(
        authorization, require_membership=False
    ) as principal:
        user = principal.user
        if not user.is_superuser:
            raise PurchasePackageAdminForbidden()
        return AdminPurchasePackagesActor(
            user_id=user.id, is_superuser=bool(user.is_superuser)
        )
