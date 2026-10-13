from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from haoai_backend.provider_quota import application
from haoai_backend.provider_quota.domain import ProviderQuotaActor
from haoai_backend.provider_quota.errors import (
    ProviderQuotaForbidden,
    ProviderQuotaInputInvalid,
    ProviderQuotaNotFound,
)
from test_provider_quota_support import FakeCipher


ADMIN = ProviderQuotaActor("admin", True)
MEMBER = ProviderQuotaActor("member", False)


class UnitOfWork:
    def __init__(self) -> None:
        self.account = SimpleNamespace(
            id="quota-1",
            name=" Gateway ",
            provider_type="new_api",
            base_url="https://gateway.example/api",
            username="cipher:user",
            password="cipher:old-password",
            is_active=1,
            sort_order=2,
            created_at=datetime(2026, 10, 1),
            updated_at=datetime(2026, 10, 2),
        )
        self.events: list[Any] = []
        self.commits = self.refreshes = self.rollbacks = self.closes = 0
        self.missing = False
        self.fail_commit: BaseException | None = None

    def list_accounts(self):
        self.events.append("list")
        return [self.account]

    def get_account(self, account_id):
        self.events.append(("get", account_id))
        return None if self.missing or account_id != self.account.id else self.account

    def add_account(self, account_id, values):
        self.events.append(("add", account_id, dict(values)))
        self.account = SimpleNamespace(
            id=account_id,
            **values,
            sort_order=0,
            created_at=datetime(2026, 10, 3),
            updated_at=datetime(2026, 10, 3),
        )
        return self.account

    def set_account_value(self, account, field, value):
        self.events.append(("set", field, value))
        setattr(account, field, value)

    def delete_account(self, account):
        self.events.append(("delete", account.id))

    def commit(self):
        self.commits += 1
        self.events.append("commit")
        if self.fail_commit is not None:
            raise self.fail_commit

    def refresh(self, account):
        self.refreshes += 1
        self.events.append("refresh")
        return account

    def rollback(self):
        self.rollbacks += 1
        self.events.append("rollback")

    def close(self):
        self.closes += 1
        self.events.append("close")


def make_factory(uow):
    calls = []

    def factory():
        calls.append(uow)
        return uow

    return factory, calls


def test_crud_preserves_legacy_projection_commit_and_blank_credential_rules():
    uow = UnitOfWork()
    factory, calls = make_factory(uow)
    cipher = FakeCipher()

    listed = application.list_accounts(factory, ADMIN, cipher)
    assert listed == [
        {
            "id": "quota-1",
            "name": " Gateway ",
            "provider_type": "new_api",
            "base_url": "https://gateway.example/api",
            "username": "user",
            "is_active": True,
            "sort_order": 2,
            "console_url": "https://gateway.example",
        }
    ]
    assert "password" not in listed[0]
    assert (uow.commits, uow.refreshes) == (0, 0)

    created = application.create_account(
        factory,
        ADMIN,
        {
            "name": "  account " * 15,
            "provider_type": "new_api",
            "base_url": " https://new.example/v1/ ",
            "username": "  owner  ",
            "password": "  initial  ",
            "is_active": False,
        },
        cipher,
    )
    assert created["name"].startswith("account   account")
    assert len(created["name"]) == 100
    assert created["base_url"] == "https://new.example"
    assert created["username"] == "owner"
    assert "password" not in created
    assert uow.account.password == "cipher:initial"
    assert uow.account.is_active == 0
    assert (uow.commits, uow.refreshes) == (1, 1)

    updated = application.update_account(
        factory,
        ADMIN,
        uow.account.id,
        {
            "name": "renamed",
            "provider_type": "new_api",
            "base_url": "https://new.example/",
            "username": "   ",
            "password": "",
            "is_active": True,
        },
        cipher,
    )
    assert updated["name"] == "renamed"
    assert uow.account.username == "cipher:owner"
    assert uow.account.password == "cipher:initial"
    assert uow.account.is_active == 1
    assert (uow.commits, uow.refreshes) == (2, 2)

    invalidated = []
    assert application.delete_account(
        factory,
        ADMIN,
        uow.account.id,
        invalidate_sessions=invalidated.append,
    ) == {"ok": True}
    assert invalidated == [uow.account.id]
    assert (uow.commits, uow.refreshes) == (3, 2)
    assert calls == [uow, uow, uow, uow]


def test_provider_specific_validation_and_lookup_order_are_legacy_compatible():
    assert application.validate_account_payload(
        {"provider_type": "deepseek", "base_url": "https://api.deepseek.com", "password": "key"}
    ) == "https://api.deepseek.com"
    with pytest.raises(ProviderQuotaInputInvalid, match="请填写登录账号与密码"):
        application.validate_account_payload(
            {"provider_type": "new_api", "base_url": "https://gateway.example", "password": "x"}
        )
    with pytest.raises(ProviderQuotaInputInvalid, match="请填写API Key"):
        application.validate_probe_payload(
            {"provider_type": "deepseek", "base_url": "https://api.deepseek.com", "password": ""}
        )
    with pytest.raises(ProviderQuotaInputInvalid, match="暂不支持的供应商架构：future"):
        application.validate_probe_payload(
            {"provider_type": "future", "base_url": "https://future.example", "password": "x"}
        )

    uow = UnitOfWork()
    uow.missing = True
    with pytest.raises(ProviderQuotaNotFound):
        application.update_account(
            lambda: uow,
            ADMIN,
            "missing",
            {"provider_type": "unsupported", "base_url": ""},
            FakeCipher(),
        )
    assert uow.events == [("get", "missing"), "rollback", "close"]


def test_failures_cleanup_and_forbidden_actor_never_open_uow():
    opened = []
    with pytest.raises(ProviderQuotaForbidden):
        application.list_accounts(
            lambda: opened.append(UnitOfWork()), MEMBER, FakeCipher()
        )
    assert opened == []

    uow = UnitOfWork()
    failure = OSError("commit failed")
    uow.fail_commit = failure
    with pytest.raises(OSError) as caught:
        application.create_account(
            lambda: uow,
            ADMIN,
            {
                "name": "gateway",
                "provider_type": "new_api",
                "base_url": "https://gateway.example",
                "username": "owner",
                "password": "password",
            },
            FakeCipher(),
        )
    assert caught.value is failure
    assert uow.rollbacks == 1 and uow.closes == 1
