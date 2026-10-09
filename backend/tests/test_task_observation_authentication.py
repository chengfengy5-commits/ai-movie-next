from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator

import pytest

from haoai_backend.shared.errors import BusinessError
from haoai_backend.task_observation.authentication import (
    resolve_account_actor,
    resolve_active_actor,
)


class FakeAuthenticationService:
    def __init__(self, *, user_id: str = "user-a", error: Exception | None = None) -> None:
        self.user_id = user_id
        self.error = error
        self.calls: list[tuple[str, bool]] = []
        self.events: list[str] = []

    @contextmanager
    def authenticated(self, authorization: str, *, require_membership: bool = False):
        self.calls.append((authorization, require_membership))
        self.events.append("entered")
        try:
            if self.error is not None:
                raise self.error
            yield SimpleNamespace(user=SimpleNamespace(id=self.user_id))
        finally:
            self.events.append("closed")


def test_account_actor_uses_public_auth_without_membership_and_closes_before_return() -> None:
    service = FakeAuthenticationService()

    actor = resolve_account_actor(service, "Bearer valid")

    assert actor.user_id == "user-a"
    assert service.calls == [("Bearer valid", False)]
    assert service.events == ["entered", "closed"]


def test_active_actor_uses_public_membership_requirement() -> None:
    service = FakeAuthenticationService(user_id="member-b")

    actor = resolve_active_actor(service, "Bearer active")

    assert actor.user_id == "member-b"
    assert service.calls == [("Bearer active", True)]
    assert service.events == ["entered", "closed"]


def test_authentication_failure_propagates_and_still_closes_auth_context() -> None:
    failure = BusinessError(403, "会员已过期")
    service = FakeAuthenticationService(error=failure)

    with pytest.raises(BusinessError) as raised:
        resolve_active_actor(service, "Bearer expired")

    assert raised.value is failure
    assert service.events == ["entered", "closed"]
    assert service.calls == [("Bearer expired", True)]
