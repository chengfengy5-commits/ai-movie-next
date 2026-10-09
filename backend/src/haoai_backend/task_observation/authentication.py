"""Adapters for the public account-only and active-member authentication contract."""

from __future__ import annotations

from typing import ContextManager, Protocol

from haoai_backend.shared.identity import TrustedActor


class AuthenticatedUser(Protocol):
    id: str


class AuthenticatedPrincipal(Protocol):
    user: AuthenticatedUser


class PublicAuthenticationService(Protocol):
    def authenticated(
        self,
        authorization: str,
        *,
        require_membership: bool = False,
    ) -> ContextManager[AuthenticatedPrincipal]: ...


def resolve_account_actor(
    service: PublicAuthenticationService,
    authorization: str,
) -> TrustedActor:
    """Authenticate a caller without imposing active-membership policy."""
    return _resolve_actor(service, authorization, require_membership=False)


def resolve_active_actor(
    service: PublicAuthenticationService,
    authorization: str,
) -> TrustedActor:
    """Authenticate a caller and require current active membership."""
    return _resolve_actor(service, authorization, require_membership=True)


def _resolve_actor(
    service: PublicAuthenticationService,
    authorization: str,
    *,
    require_membership: bool,
) -> TrustedActor:
    # Leaving the public context before returning closes auth state before business access.
    with service.authenticated(
        authorization,
        require_membership=require_membership,
    ) as principal:
        return TrustedActor(user_id=principal.user.id)
