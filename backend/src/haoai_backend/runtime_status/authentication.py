"""Account-only authentication used by the legacy public image proxy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ContextManager, Protocol

from fastapi import Request


@dataclass(frozen=True, slots=True)
class RuntimeStatusActor:
    """Minimal identity snapshot retained after the auth unit of work closes."""

    user_id: str


class _User(Protocol):
    id: str


class _AuthenticatedPrincipal(Protocol):
    user: _User


class _AuthenticationService(Protocol):
    def authenticated(
        self, authorization: str, *, require_membership: bool = False
    ) -> ContextManager[_AuthenticatedPrincipal]: ...


def create_account_only_current_user_resolver(
    authentication_service: _AuthenticationService,
):
    """Build an inert resolver that authenticates without membership gating.

    The principal is reduced to a user id inside the authentication context so
    no ORM-backed user or session escapes its owning unit of work.
    """

    def resolve(request: Request) -> RuntimeStatusActor:
        authorization = request.headers.get("authorization", "")
        with authentication_service.authenticated(
            authorization,
            require_membership=False,
        ) as principal:
            user_id = principal.user.id
            if not user_id:
                raise RuntimeError("authenticated principal has no user id")
            actor = RuntimeStatusActor(user_id=user_id)
        return actor

    return resolve


__all__ = ("RuntimeStatusActor", "create_account_only_current_user_resolver")
