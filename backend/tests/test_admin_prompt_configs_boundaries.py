from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_prompt_configs.authentication import (
    authenticate_admin_prompt_configs_actor,
)
from haoai_backend.admin_prompt_configs.domain import (
    AdminPromptConfigsActor,
    PromptConfigRecord,
    create_values,
    prompt_config_payload,
)
from haoai_backend.admin_prompt_configs.errors import (
    AdminPromptConfigNotFound,
    AdminPromptConfigsForbidden,
    AdminPromptConfigsUnavailable,
)
from haoai_backend.admin_prompt_configs.schemas import (
    AdminPromptConfigBody,
    PromptConfigListPayload,
    PromptConfigPayload,
)
from haoai_backend.authentication.errors import AuthenticationError


NOW = datetime(2026, 10, 13, 9, 30)


class Principal:
    def __init__(self, user_id: str, is_superuser: bool) -> None:
        self.user = type("User", (), {"id": user_id, "is_superuser": is_superuser})()


class FakeAuthenticationService:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.events: list[str] = []
        self.require_membership: bool | None = None

    class Context:
        def __init__(self, outer: "FakeAuthenticationService") -> None:
            self.outer = outer

        def __enter__(self) -> Principal:
            self.outer.events.append("enter")
            return self.outer.principal

        def __exit__(self, *_args: Any) -> None:
            self.outer.events.append("close")

    def authenticated(self, authorization: str, *, require_membership: bool = False):
        self.authorization = authorization
        self.require_membership = require_membership
        return self.Context(self)


def test_create_defaults_are_missing_only_and_payload_keeps_nulls_and_dates() -> None:
    assert create_values({}) == {
        "category": "",
        "name": "提示词",
        "system_prompt": "",
        "user_prompt": "",
        "sort_order": 0,
    }
    assert create_values(
        {
            "category": None,
            "name": "",
            "system_prompt": False,
            "user_prompt": None,
            "sort_order": 0,
        }
    ) == {
        "category": None,
        "name": "",
        "system_prompt": False,
        "user_prompt": None,
        "sort_order": 0,
    }

    record = PromptConfigRecord(
        "p-1", "u-1", None, "", None, "", 0, NOW, None
    )
    payload = prompt_config_payload(record)
    assert list(payload) == [
        "id", "user_id", "category", "name", "system_prompt", "user_prompt",
        "sort_order", "created_at", "updated_at",
    ]
    assert payload["category"] is None
    assert payload["system_prompt"] is None
    assert payload["created_at"] == NOW and payload["updated_at"] is None
    assert AdminPromptConfigBody == dict[str, Any]
    assert set(PromptConfigPayload.__annotations__) == set(payload)
    assert set(PromptConfigListPayload.__annotations__) == {"data", "total"}


def test_authentication_copies_local_frozen_admin_before_context_closes() -> None:
    service = FakeAuthenticationService(Principal("admin-1", True))
    actor = authenticate_admin_prompt_configs_actor(service, "Bearer public-token")
    assert service.authorization == "Bearer public-token"
    assert service.require_membership is False
    assert service.events == ["enter", "close"]
    assert actor == AdminPromptConfigsActor("admin-1", True)
    with pytest.raises(FrozenInstanceError):
        actor.user_id = "changed"  # type: ignore[misc]


def test_non_admin_is_rejected_inside_auth_context_with_legacy_detail() -> None:
    service = FakeAuthenticationService(Principal("member", False))
    with pytest.raises(AuthenticationError) as caught:
        authenticate_admin_prompt_configs_actor(service, "Bearer token")
    assert caught.value.status_code == 403
    assert caught.value.detail == "权限不足，仅管理员可执行此操作"
    assert service.events == ["enter", "close"]


def test_business_errors_keep_exact_http_status_and_chinese_detail() -> None:
    assert (AdminPromptConfigsForbidden().status_code, AdminPromptConfigsForbidden().detail) == (
        403,
        "权限不足，仅管理员可执行此操作",
    )
    assert (AdminPromptConfigNotFound().status_code, AdminPromptConfigNotFound().detail) == (
        404,
        "提示词不存在",
    )
    assert (AdminPromptConfigsUnavailable().status_code, AdminPromptConfigsUnavailable().detail) == (
        503,
        "提示词配置服务尚未接线",
    )
