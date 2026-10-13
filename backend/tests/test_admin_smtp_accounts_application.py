from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from haoai_backend.admin_smtp_accounts import application
from haoai_backend.admin_smtp_accounts.domain import AdminSmtpAccountsActor
from haoai_backend.admin_smtp_accounts.errors import (
    AdminSmtpAccountInputInvalid,
    AdminSmtpAccountNotFound,
)


ACTOR = AdminSmtpAccountsActor("admin", True)


class Cipher:
    def __init__(self):
        self.events: list[tuple[str, Any]] = []

    def encrypt(self, value: str) -> str:
        self.events.append(("encrypt", value))
        return f"cipher:{value}"

    def decrypt(self, value: str) -> str:
        self.events.append(("decrypt", value))
        return value.removeprefix("cipher:")


class Reset:
    def __init__(self, events):
        self.events = events

    def reset_if_needed(self):
        self.events.append("reset")


class UnitOfWork:
    def __init__(self, events):
        self.events = events
        self.account = SimpleNamespace(
            id="account-1", host="smtp.qq.com", port=465, user="owner@example.test",
            password="cipher:stored", daily_sent=7, is_active=1,
            created_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 2),
        )
        self.commits = self.refreshes = self.rollbacks = self.closes = 0
        self.fail_commit: BaseException | None = None
        self.fail_close: BaseException | None = None
        self.missing = False

    def list_accounts(self):
        self.events.append("list")
        return [self.account]

    def get_account(self, account_id):
        self.events.append(("get", account_id))
        return None if self.missing or account_id != self.account.id else self.account

    def add_account(self, account_id, values):
        self.events.append(("add", account_id, dict(values)))
        self.account = SimpleNamespace(id=account_id, **values, created_at=None, updated_at=None)
        return self.account

    def set_account_value(self, account, field, value):
        self.events.append(("set", field, value))
        setattr(account, field, value)

    def delete_account(self, account):
        self.events.append(("delete", account.id))

    def commit(self):
        self.commits += 1
        self.events.append("commit")
        if self.fail_commit:
            raise self.fail_commit

    def refresh(self, account):
        self.refreshes += 1
        self.events.append("refresh")
        return account

    def reset_all_daily_counts(self):
        self.events.append("reset-all")

    def rollback(self):
        self.rollbacks += 1
        self.events.append("rollback")
        raise RuntimeError("rollback failure")

    def close(self):
        self.closes += 1
        self.events.append("close")
        if self.fail_close:
            raise self.fail_close


def factory_for(uow):
    calls = []
    def factory():
        calls.append(uow)
        return uow
    return factory, calls


def test_list_get_create_update_delete_and_reset_keep_legacy_payloads_and_counts():
    events: list[Any] = []
    uow = UnitOfWork(events)
    factory, calls = factory_for(uow)
    cipher = Cipher()

    listed = application.list_smtp_accounts(factory, ACTOR, cipher, Reset(events))
    assert events[:2] == ["reset", "list"]
    assert listed["total"] == 1
    assert set(listed["data"][0]) == {
        "id", "host", "port", "user", "password", "daily_sent",
        "is_active", "created_at", "updated_at",
    }
    assert listed["data"][0]["password"] == "stored"
    assert listed["data"][0]["is_active"] is True
    assert (uow.commits, uow.refreshes) == (0, 0)
    assert calls == [uow]

    detail = application.get_smtp_account(factory, ACTOR, "account-1", cipher)
    assert detail["password"] == "stored"
    assert uow.commits == 0

    created = application.create_smtp_account(
        factory, ACTOR, {"user": "new@example.test", "password": "new-secret",
                         "port": "587", "is_active": False}, cipher,
    )
    assert created["user"] == "new@example.test"
    assert created["message"] == "添加成功"
    assert uow.commits == 1 and uow.refreshes == 1
    assert uow.account.password == "cipher:new-secret"
    assert uow.account.port == 587 and uow.account.is_active == 0
    assert uow.account.daily_sent == 0

    events.clear()
    updated = application.update_smtp_account(
        factory, ACTOR, created["id"],
        {"host": "mail.example", "password": "****", "port": "2525",
         "is_active": 0, "ignored": "value"}, cipher,
    )
    assert updated == {"message": "更新成功"}
    assert [event[1] for event in events if isinstance(event, tuple) and event[0] == "set"] == [
        "host", "port", "is_active",
    ]
    assert uow.account.password == "cipher:new-secret"
    assert uow.commits == 2 and uow.refreshes == 1

    assert application.reset_smtp_count(factory, ACTOR, created["id"]) == {"message": "已重置"}
    assert uow.account.daily_sent == 0
    assert application.delete_smtp_account(factory, ACTOR, created["id"]) == {"message": "删除成功"}
    assert uow.commits == 4 and uow.refreshes == 1


def test_conversion_and_cipher_failures_preserve_lookup_order_and_cleanup():
    events: list[Any] = []
    uow = UnitOfWork(events)
    factory, _ = factory_for(uow)
    cipher = Cipher()

    with pytest.raises(AdminSmtpAccountInputInvalid) as missing:
        application.create_smtp_account(factory, ACTOR, {"user": "", "password": "x"}, cipher)
    assert missing.value.status_code == 400
    assert uow.rollbacks == 1 and uow.closes == 1

    uow.missing = True
    cipher.events.clear()
    with pytest.raises(AdminSmtpAccountNotFound) as not_found:
        application.update_smtp_account(factory, ACTOR, "missing", {"password": "secret"}, cipher)
    assert not_found.value.detail == "账号不存在"
    assert cipher.events == []
    assert uow.rollbacks == 2 and uow.closes == 2

    uow.missing = False
    with pytest.raises(TypeError):
        application.update_smtp_account(factory, ACTOR, "account-1", {"password": 123}, cipher)
    assert uow.commits == 0
    assert uow.rollbacks == 3 and uow.closes == 3

    with pytest.raises(ValueError):
        application.create_smtp_account(
            factory, ACTOR, {"user": "x", "password": "would-not-encrypt", "port": "bad"}, cipher
        )
    assert cipher.events == []
    assert uow.rollbacks == 4 and uow.closes == 4


def test_primary_commit_failure_survives_cleanup_and_success_close_failure_propagates():
    events: list[Any] = []
    uow = UnitOfWork(events)
    factory, _ = factory_for(uow)
    primary = OSError("commit failed")
    uow.fail_commit = primary
    with pytest.raises(OSError) as error:
        application.reset_smtp_count(factory, ACTOR, "account-1")
    assert error.value is primary
    assert uow.rollbacks == 1 and uow.closes == 1

    uow.fail_commit = None
    close_error = RuntimeError("close failed")
    uow.fail_close = close_error
    with pytest.raises(RuntimeError) as error:
        application.get_smtp_account(factory, ACTOR, "account-1", Cipher())
    assert error.value is close_error
    assert uow.rollbacks == 1 and uow.closes == 2


def test_forbidden_actor_never_opens_business_unit():
    opened = []
    with pytest.raises(Exception) as error:
        application.get_smtp_account(
            lambda: opened.append(UnitOfWork([])),
            AdminSmtpAccountsActor("member", False),
            "account-1",
            Cipher(),
        )
    assert getattr(error.value, "status_code", None) == 403
    assert opened == []
