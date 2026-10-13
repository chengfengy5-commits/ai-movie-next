from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_system_config import application
from haoai_backend.admin_system_config.domain import (
    AdminSystemConfigActor,
    SystemConfigRecord,
    system_config_payload,
)
from haoai_backend.admin_system_config.errors import (
    AdminSystemConfigForbidden,
    AdminSystemConfigNotFound,
)


ADMIN = AdminSystemConfigActor(user_id="admin", is_superuser=True)
NON_ADMIN = AdminSystemConfigActor(user_id="member", is_superuser=False)
NOW = datetime(2026, 10, 13, 12, 0, 0)
UPDATE_FIELDS = (
    "muse_token",
    "site_title",
    "site_tagline",
    "storyboard_cost_multiplier",
    "membership_benefits",
    "payments_enabled",
    "tutorial_url",
    "purchase_qr_code_url",
    "purchase_contact_info",
    "team_created_limit",
    "team_joined_limit",
    "team_member_limit",
    "invite_code_ttl_hours",
    "chapter_lock_idle_minutes",
    "aistarslab_credit_multiplier",
    "frontend_url",
    "allowed_origins",
    "upload_dir",
    "worker_max_concurrency",
)


def config_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "config-1",
        "muse_token": "test-token",
        "site_title": "Hao AI",
        "site_tagline": "Unlimited",
        "storyboard_cost_multiplier": 2,
        "membership_benefits": "<b>benefits</b>",
        "payments_enabled": 1,
        "tutorial_url": "https://example.test/tutorial",
        "purchase_qr_code_url": "https://example.test/qr.png",
        "purchase_contact_info": "contact",
        "oss_access_key_id": "hidden-id",
        "oss_access_key_secret": "hidden-secret",
        "oss_bucket_name": "hidden-bucket",
        "oss_endpoint": "hidden-endpoint",
        "fc_transfer_url": "hidden-fc",
        "fc_transfer_url_cn": "hidden-fc-cn",
        "oss_haoai_match_domain": "hidden-match",
        "oss_haoai_replace_domain": "hidden-replace",
        "team_created_limit": 3,
        "team_joined_limit": 3,
        "team_member_limit": 20,
        "invite_code_ttl_hours": 24,
        "chapter_lock_idle_minutes": 15,
        "aistarslab_credit_multiplier": 1.0,
        "zhangyuge_billing_pat": "hidden-pat",
        "frontend_url": "",
        "allowed_origins": "",
        "upload_dir": "",
        "worker_max_concurrency": "",
    }
    values.update(overrides)
    return values


def record(**overrides: Any) -> SystemConfigRecord:
    return SystemConfigRecord(**config_values(**overrides))


class FakeUnitOfWork:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values
        self.events: list[Any] = []
        self.set_fields: list[str] = []
        self.raise_commit: BaseException | None = None
        self.raise_rollback = False
        self.raise_close = False

    def first_config(self):
        self.events.append("first")
        return record(**self.values) if self.values is not None else None

    def get_config(self, config_id: str):
        self.events.append(("get", config_id))
        if self.values is None or self.values["id"] != config_id:
            return None
        return record(**self.values)

    def load_config_for_update(self, config_id: str):
        self.events.append(("load", config_id))
        if self.values is None or self.values["id"] != config_id:
            return None
        return self.values

    def set_config_value(self, config: object, field: str, value: Any) -> None:
        self.events.append(("set", field, value))
        self.set_fields.append(field)
        config[field] = value

    def refresh_config(self, config: object):
        self.events.append("refresh")
        return record(**config)

    def commit(self) -> None:
        self.events.append("commit")
        if self.raise_commit is not None:
            raise self.raise_commit

    def rollback(self) -> None:
        self.events.append("rollback")
        if self.raise_rollback:
            raise RuntimeError("rollback failed")

    def close(self) -> None:
        self.events.append("close")
        if self.raise_close:
            raise RuntimeError("close failed")


def test_projection_and_single_row_reads_hide_credentials_and_preserve_defaults() -> None:
    projected = system_config_payload(
        record(
            membership_benefits=None,
            payments_enabled=0,
            tutorial_url=None,
            team_created_limit=0,
            team_joined_limit=None,
            team_member_limit=0,
            invite_code_ttl_hours=0,
            chapter_lock_idle_minutes=0,
            aistarslab_credit_multiplier=0.0,
            frontend_url=None,
            allowed_origins=None,
            upload_dir=None,
            worker_max_concurrency=None,
        )
    )
    assert len(projected) == 20
    assert projected["membership_benefits"] == ""
    assert projected["payments_enabled"] is False
    assert projected["tutorial_url"] == ""
    assert projected["team_created_limit"] == 3
    assert projected["team_joined_limit"] == 3
    assert projected["team_member_limit"] == 20
    assert projected["invite_code_ttl_hours"] == 24
    assert projected["chapter_lock_idle_minutes"] == 15
    assert projected["aistarslab_credit_multiplier"] == 1.0
    hidden = {
        "oss_access_key_id",
        "oss_access_key_secret",
        "oss_bucket_name",
        "oss_endpoint",
        "fc_transfer_url",
        "fc_transfer_url_cn",
        "oss_haoai_match_domain",
        "oss_haoai_replace_domain",
        "zhangyuge_billing_pat",
    }
    assert hidden.isdisjoint(projected)

    unit = FakeUnitOfWork(config_values())
    assert application.list_system_config(lambda: unit, ADMIN)["total"] == 1
    assert unit.events == ["first", "close"]
    assert application.list_system_config(
        lambda: FakeUnitOfWork(None), ADMIN
    ) == {"data": [], "total": 0}
    with pytest.raises(AdminSystemConfigNotFound) as missing:
        application.get_system_config(lambda: FakeUnitOfWork(None), ADMIN, "missing")
    assert missing.value.status_code == 404
    assert missing.value.detail == "配置不存在"


def test_update_keeps_fixed_field_order_coercions_and_unknown_only_write() -> None:
    unit = FakeUnitOfWork(config_values())
    body = {
        "worker_max_concurrency": None,
        "upload_dir": "  /tmp/uploads  ",
        "unknown": "ignored",
        "site_title": None,
        "payments_enabled": [],
        "storyboard_cost_multiplier": "8",
        "membership_benefits": None,
        "aistarslab_credit_multiplier": "invalid",
        "frontend_url": "  https://front.example.test  ",
        "allowed_origins": None,
        "team_created_limit": "12",
    }
    result = application.update_system_config(lambda: unit, ADMIN, "config-1", body)
    assert unit.set_fields == [
        field for field in UPDATE_FIELDS if field in body
    ]
    assert unit.values["site_title"] is None
    assert unit.values["storyboard_cost_multiplier"] == 8
    assert unit.values["membership_benefits"] is None
    assert unit.values["payments_enabled"] == 0
    assert unit.values["team_created_limit"] == 12
    assert unit.values["aistarslab_credit_multiplier"] == 1.0
    assert unit.values["frontend_url"] == "https://front.example.test"
    assert unit.values["allowed_origins"] == ""
    assert unit.values["upload_dir"] == "/tmp/uploads"
    assert unit.values["worker_max_concurrency"] == "0"
    assert result["site_title"] is None
    assert unit.events[-3:] == ["commit", "refresh", "close"]

    unknown_only = FakeUnitOfWork(config_values())
    application.update_system_config(
        lambda: unknown_only, ADMIN, "config-1", {"unknown": True}
    )
    assert unknown_only.set_fields == []
    assert unknown_only.events == [
        ("load", "config-1"),
        "commit",
        "refresh",
        "close",
    ]


def test_all_nineteen_updates_follow_source_order_and_success_close_errors_propagate() -> None:
    values = {
        "muse_token": "token",
        "site_title": "title",
        "site_tagline": "tagline",
        "storyboard_cost_multiplier": "3",
        "membership_benefits": "benefits",
        "payments_enabled": "false",
        "tutorial_url": "https://example.test/tutorial",
        "purchase_qr_code_url": "https://example.test/qr",
        "purchase_contact_info": "contact",
        "team_created_limit": "4",
        "team_joined_limit": "5",
        "team_member_limit": "6",
        "invite_code_ttl_hours": "7",
        "chapter_lock_idle_minutes": "8",
        "aistarslab_credit_multiplier": "2.5",
        "frontend_url": " https://front.example.test ",
        "allowed_origins": " https://origin.example.test ",
        "upload_dir": " /tmp/uploads ",
        "worker_max_concurrency": " 9 ",
    }
    unit = FakeUnitOfWork(config_values())
    body = {field: values[field] for field in reversed(UPDATE_FIELDS)}
    application.update_system_config(lambda: unit, ADMIN, "config-1", body)
    assert unit.set_fields == list(UPDATE_FIELDS)

    close_error = RuntimeError("success close failed")

    class CloseFailure(FakeUnitOfWork):
        def close(self) -> None:
            self.events.append("close")
            raise close_error

    close_failure = CloseFailure(config_values())
    with pytest.raises(RuntimeError) as caught:
        application.list_system_config(lambda: close_failure, ADMIN)
    assert caught.value is close_error
    assert close_failure.events == ["first", "close"]


def test_update_loads_before_conversion_and_preserves_primary_cleanup_error() -> None:
    missing = FakeUnitOfWork(None)
    with pytest.raises(AdminSystemConfigNotFound):
        application.update_system_config(
            lambda: missing,
            ADMIN,
            "missing",
            {"storyboard_cost_multiplier": "not-an-int"},
        )
    assert missing.events == [("load", "missing"), "rollback", "close"]

    primary = RuntimeError("commit failed")
    broken = FakeUnitOfWork(config_values())
    broken.raise_commit = primary
    broken.raise_rollback = True
    broken.raise_close = True
    with pytest.raises(RuntimeError) as caught:
        application.update_system_config(
            lambda: broken, ADMIN, "config-1", {"site_title": "Updated"}
        )
    assert caught.value is primary
    assert broken.events[-4:] == [
        ("set", "site_title", "Updated"),
        "commit",
        "rollback",
        "close",
    ]

    conversion_error = FakeUnitOfWork(config_values())
    with pytest.raises(ValueError):
        application.update_system_config(
            lambda: conversion_error,
            ADMIN,
            "config-1",
            {"site_title": "rolled back", "storyboard_cost_multiplier": "bad"},
        )
    assert conversion_error.events[-2:] == ["rollback", "close"]

    non_string_deployment_value = FakeUnitOfWork(config_values())
    with pytest.raises(AttributeError):
        application.update_system_config(
            lambda: non_string_deployment_value,
            ADMIN,
            "config-1",
            {"frontend_url": 17},
        )
    assert non_string_deployment_value.events[-2:] == ["rollback", "close"]

    non_admin = FakeUnitOfWork(config_values())
    with pytest.raises(AdminSystemConfigForbidden):
        application.list_system_config(lambda: non_admin, NON_ADMIN)
    assert non_admin.events == []
