from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from haoai_backend.admin_users import application
from haoai_backend.admin_users.domain import AdminUsersActor, UserRecord
from haoai_backend.admin_users.errors import AdminUsersBadRequest, AdminUsersForbidden
from haoai_backend.admin_users.schemas import UserCreateBody

NOW = datetime(2026, 10, 13, 1, 2, 3)
ADMIN = AdminUsersActor("admin", True)


class FakeHasher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.passwords: list[str] = []

    def hash(self, password: str) -> str:
        self.events.append("hash")
        self.passwords.append(password)
        return f"hashed:{password}"

    def verify(self, password: str, encoded: str) -> bool:
        raise AssertionError("verify is not used by administrator user CRUD")


class FakeUnitOfWork:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.username_duplicate = False
        self.email_duplicate = False
        self.user = SimpleNamespace(
            id="target",
            username="old",
            email="old@example.test",
            is_superuser=False,
            membership_type="free",
            membership_expires_at=None,
            created_at=NOW,
        )
        self.credit = SimpleNamespace(credits=4)
        self.records = [
            UserRecord(
                id="target",
                username="target",
                email="target@example.test",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                credits=7,
                created_at=NOW,
            )
        ]
        self.loaded_user: Any | None = self.user
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.refresh_count = 0
        self.created_user_args: dict[str, Any] | None = None
        self.credit_args: tuple[str, int] | None = None
        self.changed_fields: list[tuple[str, Any]] = []
        self.list_args: dict[str, Any] | None = None
        self.fail_record_read: BaseException | None = None
        self.fail_rollback = False
        self.fail_close = False
        self.fail_close_on_success = False
        self.delete_steps: list[str] = []

    def username_exists(self, username: str) -> bool:
        self.events.append("username_exists")
        return self.username_duplicate

    def email_exists(self, email: str) -> bool:
        self.events.append("email_exists")
        return self.email_duplicate

    def create_user(self, **kwargs: Any) -> Any:
        self.events.append("create_user")
        self.created_user_args = kwargs
        self.user = SimpleNamespace(
            id="created",
            username=kwargs["username"],
            email=kwargs["email"],
            is_superuser=kwargs["is_superuser"],
            membership_type=kwargs["membership_type"],
            membership_expires_at=kwargs["membership_expires_at"],
            created_at=NOW,
        )
        return self.user

    def create_user_credit(self, user_id: str, credits: int) -> Any:
        self.events.append("create_user_credit")
        self.credit_args = (user_id, credits)
        self.credit = SimpleNamespace(credits=credits)
        return self.credit

    def refresh_user(self, row: Any) -> UserRecord:
        self.events.append("refresh_user")
        self.refresh_count += 1
        return UserRecord(
            id=row.id,
            username=row.username,
            email=row.email,
            is_superuser=row.is_superuser,
            membership_type=row.membership_type,
            membership_expires_at=row.membership_expires_at,
            credits=0,
            created_at=row.created_at,
        )

    def list_users(self, **kwargs: Any) -> tuple[list[UserRecord], int]:
        self.events.append("list_users")
        self.list_args = kwargs
        return self.records, 1

    def get_user_record(self, user_id: str) -> UserRecord | None:
        self.events.append("get_user_record")
        if self.fail_record_read is not None:
            raise self.fail_record_read
        return next((row for row in self.records if row.id == user_id), None)

    def load_user(self, user_id: str) -> Any | None:
        self.events.append("load_user")
        if self.loaded_user is not None and self.loaded_user.id == user_id:
            return self.loaded_user
        return None

    def set_user_field(self, row: Any, field: str, value: Any) -> None:
        self.events.append(f"set:{field}")
        self.changed_fields.append((field, value))
        setattr(row, field, value)

    def get_user_credit(self, user_id: str) -> Any | None:
        self.events.append("get_user_credit")
        return self.credit

    def set_credit_amount(self, row: Any, credits: int) -> None:
        self.events.append("set_credit_amount")
        row.credits = credits

    def delete_related_user_data(self, user_id: str) -> None:
        self.events.append("delete_related_user_data")
        self.delete_steps.append(f"related:{user_id}")

    def delete_user(self, row: Any) -> None:
        self.events.append("delete_user")
        self.delete_steps.append(f"user:{row.id}")

    def commit(self) -> None:
        self.events.append("commit")
        self.commit_count += 1

    def rollback(self) -> None:
        self.events.append("rollback")
        self.rollback_count += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")

    def close(self) -> None:
        self.events.append("close")
        self.close_count += 1
        if self.fail_close or self.fail_close_on_success:
            raise RuntimeError("close failed")


def test_create_checks_username_then_email_before_expiry_and_hashing() -> None:
    events: list[str] = []
    unit = FakeUnitOfWork(events)
    unit.username_duplicate = True
    hasher = FakeHasher(events)
    body = UserCreateBody(
        username="taken",
        email="taken@example.test",
        password="secret",
        membership_expires_at="not-a-date",
    )

    with pytest.raises(AdminUsersBadRequest, match="用户名已存在"):
        application.create_user(lambda: unit, ADMIN, body, hasher)

    assert events == ["username_exists", "rollback", "close"]
    assert hasher.passwords == []
    assert unit.commit_count == 0

    events.clear()
    unit.username_duplicate = False
    unit.email_duplicate = True
    with pytest.raises(AdminUsersBadRequest, match="该邮箱已被其他账号绑定"):
        application.create_user(lambda: unit, ADMIN, body, hasher)

    assert events == ["username_exists", "email_exists", "rollback", "close"]
    assert hasher.passwords == []


def test_create_parses_expiry_hashes_once_commits_and_refreshes_actual_user() -> None:
    events: list[str] = []
    unit = FakeUnitOfWork(events)
    hasher = FakeHasher(events)
    body = UserCreateBody(
        username="new-user",
        email="new@example.test",
        password="secret",
        is_superuser=True,
        membership_type="premium",
        membership_expires_at="2027-01-02T03:04:05Z",
        credits=-3,
    )

    payload = application.create_user(lambda: unit, ADMIN, body, hasher)

    assert events == [
        "username_exists",
        "email_exists",
        "hash",
        "create_user",
        "create_user_credit",
        "commit",
        "refresh_user",
        "close",
    ]
    assert hasher.passwords == ["secret"]
    assert unit.created_user_args is not None
    assert unit.created_user_args["hashed_password"] == "hashed:secret"
    assert unit.created_user_args["membership_expires_at"] == datetime(
        2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )
    assert unit.credit_args == ("created", -3)
    assert unit.commit_count == 1
    assert unit.refresh_count == 1
    assert set(payload) == {
        "id",
        "username",
        "email",
        "is_superuser",
        "membership_type",
        "membership_expires_at",
        "credits",
        "created_at",
    }
    assert payload["credits"] == -3
    assert payload["id"] == "created"


def test_create_invalid_expiry_is_a_400_before_password_hashing() -> None:
    events: list[str] = []
    unit = FakeUnitOfWork(events)
    hasher = FakeHasher(events)
    body = UserCreateBody(
        username="new-user",
        email="new@example.test",
        password="secret",
        membership_expires_at="bad",
    )

    with pytest.raises(AdminUsersBadRequest, match="会员到期时间格式不正确"):
        application.create_user(lambda: unit, ADMIN, body, hasher)

    assert events == ["username_exists", "email_exists", "rollback", "close"]
    assert hasher.passwords == []
    assert unit.commit_count == 0


def test_list_passes_typed_filters_and_returns_the_eight_field_dto() -> None:
    unit = FakeUnitOfWork()
    payload = application.list_users(
        lambda: unit,
        ADMIN,
        skip=2,
        limit=5,
        sort_field="bio",
        sort_order="asc",
        username="%",
        email="example",
        membership_type="free",
        is_superuser=False,
    )

    assert unit.list_args == {
        "skip": 2,
        "limit": 5,
        "sort_field": "bio",
        "sort_order": "asc",
        "username": "%",
        "email": "example",
        "membership_type": "free",
        "is_superuser": False,
    }
    assert payload["total"] == 1
    assert set(payload["data"][0]) == {
        "id",
        "username",
        "email",
        "is_superuser",
        "membership_type",
        "membership_expires_at",
        "credits",
        "created_at",
    }


def test_update_keeps_raw_body_order_and_legacy_self_guard_values() -> None:
    events: list[str] = []
    unit = FakeUnitOfWork(events)
    unit.user.id = "admin"
    unit.user.username = "old"
    unit.user.membership_type = "free"
    unit.user.membership_expires_at = None
    unit.user.is_superuser = True
    body = {
        "membership_type": "premium",
        "membership_expires_at": "invalid",
        "username": "new",
        "credits": True,
        "is_superuser": 0,
    }

    payload = application.update_user(lambda: unit, ADMIN, "admin", body)

    assert payload == {"message": "用户更新成功"}
    assert unit.changed_fields == [
        ("membership_type", "premium"),
        ("membership_expires_at", "invalid"),
        ("username", "new"),
        ("is_superuser", 0),
    ]
    assert unit.credit.credits == 1
    assert unit.commit_count == 1
    assert unit.refresh_count == 0
    assert events[-1] == "close"


def test_update_self_guard_is_exact_and_runs_after_user_lookup() -> None:
    unit = FakeUnitOfWork()
    unit.user.id = "admin"

    with pytest.raises(AdminUsersBadRequest, match="不能取消自己的管理员权限"):
        application.update_user(
            lambda: unit,
            ADMIN,
            "admin",
            {"is_superuser": False},
        )

    assert unit.events[:2] == ["load_user", "rollback"]
    assert unit.commit_count == 0

    unit = FakeUnitOfWork()
    unit.user.id = "admin"
    application.update_user(lambda: unit, ADMIN, "admin", {"is_superuser": 0})
    assert unit.user.is_superuser == 0


def test_update_nan_failure_rolls_back_and_closes_without_commit() -> None:
    unit = FakeUnitOfWork()

    with pytest.raises(ValueError):
        application.update_user(
            lambda: unit,
            ADMIN,
            "target",
            {"credits": float("nan")},
        )

    assert unit.commit_count == 0
    assert unit.rollback_count == 1
    assert unit.close_count == 1


def test_delete_runs_related_cleanup_then_user_delete_and_one_commit() -> None:
    unit = FakeUnitOfWork()

    payload = application.delete_user(lambda: unit, ADMIN, "target")

    assert payload == {"success": True, "message": "用户 old 已删除"}
    assert unit.delete_steps == ["related:target", "user:target"]
    assert unit.commit_count == 1
    assert unit.refresh_count == 0
    assert unit.events[-1] == "close"


def test_actor_check_precedes_business_factory_creation() -> None:
    factory_calls = 0

    def factory() -> FakeUnitOfWork:
        nonlocal factory_calls
        factory_calls += 1
        return FakeUnitOfWork()

    with pytest.raises(AdminUsersForbidden):
        application.list_users(
            factory,
            AdminUsersActor("regular", False),
            skip=0,
            limit=10,
            sort_field="created_at",
            sort_order="desc",
            username=None,
            email=None,
            membership_type=None,
            is_superuser=None,
        )

    assert factory_calls == 0


def test_main_failure_survives_independent_rollback_and_close_failures() -> None:
    unit = FakeUnitOfWork()
    unit.fail_record_read = LookupError("primary lookup failure")
    unit.fail_rollback = True
    unit.fail_close = True

    with pytest.raises(LookupError, match="primary lookup failure"):
        application.get_user(lambda: unit, ADMIN, "target")

    assert unit.events == ["get_user_record", "rollback", "close"]
    assert unit.rollback_count == 1
    assert unit.close_count == 1


def test_successful_operation_propagates_close_failure() -> None:
    unit = FakeUnitOfWork()
    unit.fail_close_on_success = True

    with pytest.raises(RuntimeError, match="close failed"):
        application.get_user(lambda: unit, ADMIN, "target")

    assert unit.rollback_count == 0
    assert unit.close_count == 1
