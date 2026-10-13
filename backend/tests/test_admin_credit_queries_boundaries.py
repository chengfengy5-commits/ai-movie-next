from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import Any

from fastapi.routing import APIRoute
import pytest

from haoai_backend.admin_credit_queries.authentication import (
    authenticate_admin_credit_queries_actor,
)
from haoai_backend.admin_credit_queries.domain import (
    AdminCreditQueriesActor,
    CreditLogRecord,
    UserCreditRecord,
)
from haoai_backend.admin_credit_queries.errors import (
    AdminCreditQueriesUnavailable,
    UserCreditNotFound,
)
from haoai_backend.admin_credit_queries.http import build_admin_credit_queries_router
from haoai_backend.admin_credit_queries.schemas import (
    CreditLogListPayload,
    CreditLogPayload,
    UserCreditListPayload,
    UserCreditPayload,
    credit_log_payload,
    user_credit_payload,
)
from haoai_backend.admin_credit_queries.tables import (
    Base,
    CreditLogRow,
    UserCreditRow,
    UserRow,
    billing_units_reference,
)
from haoai_backend.authentication.errors import AuthenticationError

NOW = datetime(2026, 10, 13, 10, 0)


def test_public_payloads_and_private_mappings_match_the_fixed_projection() -> None:
    assert set(UserCreditPayload.__annotations__) == {
        "id",
        "user_id",
        "credits",
        "created_at",
        "updated_at",
    }
    assert set(CreditLogPayload.__annotations__) == {
        "id",
        "user_id",
        "amount",
        "balance_after",
        "type",
        "description",
        "task_id",
        "created_at",
    }
    assert set(UserCreditListPayload.__annotations__) == {"data", "total"}
    assert set(CreditLogListPayload.__annotations__) == {"data", "total"}

    assert list(UserRow.__table__.columns.keys()) == [
        "id",
        "username",
        "email",
        "hashed_password",
        "is_superuser",
        "membership_type",
        "membership_expires_at",
        "avatar_url",
        "bio",
        "created_at",
        "password_updated_at",
    ]
    assert list(UserCreditRow.__table__.columns.keys()) == [
        "id",
        "user_id",
        "credits",
        "created_at",
        "updated_at",
    ]
    assert list(CreditLogRow.__table__.columns.keys()) == [
        "id",
        "user_id",
        "amount",
        "balance_after",
        "type",
        "description",
        "task_id",
        "business_key",
        "billing_unit_id",
        "related_debit_id",
        "created_at",
    ]
    assert next(
        constraint
        for constraint in CreditLogRow.__table__.constraints
        if constraint.name == "uq_credit_logs_business_key"
    )
    foreign_keys = {
        key.name
        for column in CreditLogRow.__table__.columns
        for key in column.foreign_keys
    }
    assert foreign_keys == {
        None,
        "fk_credit_logs_billing_unit_id_billing_units",
        "fk_credit_logs_related_debit_id_credit_logs",
    }
    assert set(UserRow.__mapper__.relationships.keys()) == {
        "credit",
        "credit_logs",
    }
    assert billing_units_reference.info["external_reference_only"] is True
    assert "billing_units" in Base.metadata.tables
    assert not any(
        mapper.local_table is billing_units_reference
        for mapper in Base.registry.mappers
    )

    credit_payload = user_credit_payload(
        UserCreditRecord("credit", "user", 0, NOW, NOW)
    )
    log_payload = credit_log_payload(
        CreditLogRecord("log", "user", -1, 0, "usage", None, None, NOW)
    )
    assert list(credit_payload) == [
        "id",
        "user_id",
        "credits",
        "created_at",
        "updated_at",
    ]
    assert list(log_payload) == [
        "id",
        "user_id",
        "amount",
        "balance_after",
        "type",
        "description",
        "task_id",
        "created_at",
    ]
    assert "business_key" not in log_payload
    assert "billing_unit_id" not in log_payload
    assert "related_debit_id" not in log_payload


class _Principal:
    def __init__(self, user_id: str, is_superuser: bool) -> None:
        self.user = type(
            "AuthUser",
            (),
            {"id": user_id, "is_superuser": is_superuser},
        )()


class _AuthenticationService:
    def __init__(self, user_id: str, is_superuser: bool) -> None:
        self.principal = _Principal(user_id, is_superuser)
        self.events: list[str] = []
        self.require_membership: bool | None = None
        self.authorization = ""

    class _Context:
        def __init__(self, outer: "_AuthenticationService") -> None:
            self.outer = outer

        def __enter__(self):
            self.outer.events.append("enter")
            return self.outer.principal

        def __exit__(self, *_args: Any) -> None:
            self.outer.events.append("close")
            self.outer.principal.user = None

    def authenticated(self, authorization: str, *, require_membership: bool = False):
        self.authorization = authorization
        self.require_membership = require_membership
        return self._Context(self)


def test_auth_copies_actor_before_close_and_preserves_public_auth_errors() -> None:
    service = _AuthenticationService("free-admin", True)
    actor = authenticate_admin_credit_queries_actor(service, "Bearer token")
    assert service.authorization == "Bearer token"
    assert service.require_membership is False
    assert service.events == ["enter", "close"]
    assert actor == AdminCreditQueriesActor("free-admin", True)
    with pytest.raises(FrozenInstanceError):
        actor.user_id = "changed"  # type: ignore[misc]

    denied = _AuthenticationService("member", False)
    try:
        authenticate_admin_credit_queries_actor(denied, "Bearer token")
    except AuthenticationError as error:
        assert error.status_code == 403
        assert error.detail == "权限不足，仅管理员可执行此操作"
    else:
        raise AssertionError("non-admin must be denied after the auth context closes")
    assert denied.events == ["enter", "close"]

    challenge = AuthenticationError(401, "无法验证凭据", authenticate=True)
    assert challenge.headers == {"WWW-Authenticate": "Bearer"}
    assert UserCreditNotFound().status_code == 404
    assert AdminCreditQueriesUnavailable().status_code == 503


def test_router_is_inert_and_exposes_only_the_four_fixed_routes() -> None:
    router = build_admin_credit_queries_router(
        authentication_service=None,
        session_factory=None,
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {
        (route.path, tuple(sorted(route.methods or ())))
        for route in routes
    } == {
        ("/api/admin/user-credits", ("GET",)),
        ("/api/admin/user-credits/{credit_id}", ("GET",)),
        ("/api/admin/user-credits/{credit_id}", ("PUT",)),
        ("/api/admin/credit-logs", ("GET",)),
    }
    assert all(route.response_model is None for route in routes)

    calls: list[str] = []

    def forbidden_factory():
        calls.append("factory")
        raise AssertionError("building a router must not open a session")

    cold_router = build_admin_credit_queries_router(
        authentication_service=None,
        session_factory=forbidden_factory,
    )
    assert calls == []
    assert len(cold_router.routes) == 4
