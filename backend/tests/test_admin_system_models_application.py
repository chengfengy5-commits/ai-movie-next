from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from haoai_backend.admin_system_models import application
from haoai_backend.admin_system_models.domain import (
    AdminSystemModelActor,
    SystemModelRecord,
)
from haoai_backend.admin_system_models.errors import (
    AdminSystemModelNotFound,
    AdminSystemModelsForbidden,
)


ADMIN = AdminSystemModelActor(user_id="admin", is_superuser=True)
NON_ADMIN = AdminSystemModelActor(user_id="member", is_superuser=False)
NOW = datetime(2026, 10, 13, 12, 0, 0)


def record(values: dict[str, Any]) -> SystemModelRecord:
    return SystemModelRecord(
        id=values["id"],
        category=values["category"],
        name=values["name"],
        provider=values["provider"],
        api_key=values["api_key"],
        model=values["model"],
        base_url=values["base_url"],
        cost_per_call=values["cost_per_call"],
        description=values["description"],
        is_active=values["is_active"],
        sort_order=values["sort_order"],
        params=values.get("params"),
        created_at=values.get("created_at", NOW),
        updated_at=values.get("updated_at", NOW),
    )


class PrefixCipher:
    def __init__(self) -> None:
        self.encrypted: list[str | None] = []
        self.decrypted: list[str | None] = []

    def encrypt(self, plain_text: str | None) -> str:
        self.encrypted.append(plain_text)
        return f"stored:{plain_text}" if plain_text else ""

    def decrypt(self, cipher_text: str | None) -> str:
        self.decrypted.append(cipher_text)
        return f"opened:{cipher_text}" if cipher_text else ""


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, Any]] = {}
        self.events: list[str] = []
        self.last_added: dict[str, Any] | None = None

    def list_models(self, **filters: Any):
        self.events.append("list")
        self.filters = filters
        rows = tuple(record(row) for row in self.models.values())
        return rows, len(rows)

    def get_model(self, model_id: str):
        self.events.append("get")
        values = self.models.get(model_id)
        return record(values) if values is not None else None

    def add_model(self, model_id: str, values: dict[str, Any]) -> object:
        self.events.append("add")
        row = {
            "id": model_id,
            **values,
            "params": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.models[model_id] = row
        self.last_added = row
        return row

    def load_model_for_update(self, model_id: str) -> object | None:
        self.events.append("load_update")
        return self.models.get(model_id)

    def set_model_values(self, model: object, changes: dict[str, Any]) -> None:
        self.events.append("set")
        model.update(changes)

    def refresh_model(self, model: object) -> SystemModelRecord:
        self.events.append("refresh")
        return record(model)

    def delete_model(self, model_id: str) -> bool:
        self.events.append("delete")
        return self.models.pop(model_id, None) is not None

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def test_create_list_detail_and_update_keep_legacy_key_and_params_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = FakeUnitOfWork()
    cipher = PrefixCipher()
    factories: list[FakeUnitOfWork] = []

    def factory() -> FakeUnitOfWork:
        factories.append(unit)
        return unit

    monkeypatch.setattr(application, "uuid4", lambda: "generated-id")
    created = application.create_system_model(
        factory,
        ADMIN,
        {
            "category": "video",
            "name": "V",
            "provider": "vendor",
            "api_key": "gAAAAA-client-looking-value",
            "params": "{\"validated\":true}",
            "cost_per_call": 7,
        },
        cipher,
    )

    assert created["id"] == "generated-id"
    assert created["api_key"] == "stored:gAAAAA-client-looking-value"
    assert created["params"] is None
    assert created["model"] == created["base_url"] == created["description"] == ""
    assert created["cost_per_call"] == 7
    assert len(created) == 14
    assert cipher.encrypted == ["gAAAAA-client-looking-value"]
    assert unit.events == ["add", "commit", "refresh", "close"]

    listed = application.list_system_models(
        factory,
        ADMIN,
        category="",
        provider="",
        is_active=False,
        skip=3,
        limit=2,
    )
    assert listed["data"][0]["api_key"] == "stored:gAAAAA-client-looking-value"
    assert cipher.decrypted == []
    assert unit.filters == {
        "category": "",
        "provider": "",
        "is_active": False,
        "skip": 3,
        "limit": 2,
    }

    detailed = application.get_system_model(factory, ADMIN, "generated-id", cipher)
    assert detailed["api_key"] == "opened:stored:gAAAAA-client-looking-value"
    assert cipher.decrypted == ["stored:gAAAAA-client-looking-value"]

    before = unit.models["generated-id"]["api_key"]
    for value in (None, "", "gAAAAA-client-looking-value"):
        updated = application.update_system_model(
            factory, ADMIN, "generated-id", {"api_key": value}, cipher
        )
        assert updated["api_key"] == before
    assert cipher.encrypted == ["gAAAAA-client-looking-value"]

    updated = application.update_system_model(
        factory,
        ADMIN,
        "generated-id",
        {"api_key": "new-key", "base_url": None, "params": None, "ignored": "x"},
        cipher,
    )
    assert updated["api_key"] == "stored:new-key"
    assert updated["base_url"] is None
    assert updated["params"] is None
    assert "ignored" not in updated
    assert cipher.encrypted == ["gAAAAA-client-looking-value", "new-key"]


def test_business_failures_preserve_primary_exception_and_success_close_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = RuntimeError("commit failed")
    events: list[str] = []

    class BrokenUnitOfWork(FakeUnitOfWork):
        def commit(self) -> None:
            events.append("commit")
            raise primary

        def rollback(self) -> None:
            events.append("rollback")
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            events.append("close")
            raise RuntimeError("close failed")

    with pytest.raises(RuntimeError) as caught:
        application.create_system_model(
            BrokenUnitOfWork,
            ADMIN,
            {"category": "text", "name": "N", "provider": "P"},
            PrefixCipher(),
        )
    assert caught.value is primary
    assert events == ["commit", "rollback", "close"]

    close_error = RuntimeError("success close failed")

    class CloseFailure(FakeUnitOfWork):
        def close(self) -> None:
            raise close_error

    with pytest.raises(RuntimeError) as caught_close:
        application.list_system_models(
            CloseFailure,
            ADMIN,
            category=None,
            provider=None,
            is_active=None,
            skip=0,
            limit=10,
        )
    assert caught_close.value is close_error


def test_admin_check_and_not_found_happen_before_mutation() -> None:
    opened = False

    def factory():
        nonlocal opened
        opened = True
        return FakeUnitOfWork()

    with pytest.raises(AdminSystemModelsForbidden) as forbidden:
        application.list_system_models(
            factory,
            NON_ADMIN,
            category=None,
            provider=None,
            is_active=None,
            skip=0,
            limit=10,
        )
    assert forbidden.value.detail == "权限不足，仅管理员可执行此操作"
    assert not opened

    unit = FakeUnitOfWork()
    with pytest.raises(AdminSystemModelNotFound) as missing:
        application.delete_system_model(lambda: unit, ADMIN, "absent")
    assert missing.value.detail == "系统模型不存在"
    assert unit.events == ["delete", "rollback", "close"]



def test_update_checks_missing_model_before_encrypting_and_cleans_up_cipher_errors() -> None:
    class ThrowingCipher:
        def __init__(self, error: BaseException) -> None:
            self.error = error
            self.calls = 0

        def encrypt(self, plain_text: str | None) -> str:
            self.calls += 1
            raise self.error

        def decrypt(self, cipher_text: str | None) -> str:
            raise AssertionError("update must not decrypt")

    missing_unit = FakeUnitOfWork()
    missing_cipher = ThrowingCipher(RuntimeError("cipher must not run"))
    with pytest.raises(AdminSystemModelNotFound) as missing:
        application.update_system_model(
            lambda: missing_unit,
            ADMIN,
            "absent",
            {"api_key": "plain-test-key"},
            missing_cipher,
        )
    assert missing.value.detail == "系统模型不存在"
    assert missing_cipher.calls == 0
    assert missing_unit.events == ["load_update", "rollback", "close"]

    unit = FakeUnitOfWork()
    unit.add_model(
        "present",
        {
            "category": "text",
            "name": "Existing",
            "provider": "fixture",
            "api_key": "stored:old-test-key",
            "model": "",
            "base_url": "",
            "cost_per_call": 0,
            "description": "",
            "is_active": True,
            "sort_order": 0,
        },
    )
    unit.events.clear()
    cipher_error = RuntimeError("cipher failed")
    cipher = ThrowingCipher(cipher_error)

    with pytest.raises(RuntimeError) as caught:
        application.update_system_model(
            lambda: unit,
            ADMIN,
            "present",
            {"api_key": "plain-test-key"},
            cipher,
        )
    assert caught.value is cipher_error
    assert cipher.calls == 1
    assert unit.models["present"]["api_key"] == "stored:old-test-key"
    assert unit.events == ["load_update", "rollback", "close"]
