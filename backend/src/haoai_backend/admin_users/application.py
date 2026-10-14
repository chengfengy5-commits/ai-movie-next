"""Use cases for the legacy administrator user CRUD routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from haoai_backend.authentication.ports import PasswordHasher

from .domain import AdminUsersActor
from .errors import (
    AdminUserNotFound,
    AdminUsersBadRequest,
    AdminUsersForbidden,
    AdminUsersUnavailable,
)
from .ports import AdminUsersUnitOfWork, UnitOfWorkFactory
from .schemas import (
    UserCreateBody,
    UserDeletePayload,
    UserListPayload,
    UserMessagePayload,
    UserPayload,
    user_payload,
)


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[AdminUsersUnitOfWork]:
    if factory is None:
        raise AdminUsersUnavailable()
    unit_of_work = factory()
    try:
        yield unit_of_work
    except BaseException:
        try:
            unit_of_work.rollback()
        except BaseException:
            pass
        try:
            unit_of_work.close()
        except BaseException:
            pass
        raise
    else:
        unit_of_work.close()


def _require_admin(actor: AdminUsersActor) -> None:
    if not actor.is_superuser:
        raise AdminUsersForbidden()


def create_user(
    factory: UnitOfWorkFactory | None,
    actor: AdminUsersActor,
    data: UserCreateBody,
    password_hasher: PasswordHasher,
) -> UserPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        if unit_of_work.username_exists(data.username):
            raise AdminUsersBadRequest("用户名已存在")
        if unit_of_work.email_exists(data.email):
            raise AdminUsersBadRequest("该邮箱已被其他账号绑定")

        expires_at = None
        if data.membership_expires_at:
            try:
                expires_at = datetime.fromisoformat(
                    data.membership_expires_at.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise AdminUsersBadRequest("会员到期时间格式不正确") from error

        row = unit_of_work.create_user(
            username=data.username,
            email=data.email,
            hashed_password=password_hasher.hash(data.password),
            is_superuser=data.is_superuser,
            membership_type=data.membership_type,
            membership_expires_at=expires_at,
        )
        unit_of_work.create_user_credit(row.id, data.credits)
        unit_of_work.commit()
        record = unit_of_work.refresh_user(row)
        return user_payload(record, credits=data.credits)


def list_users(
    factory: UnitOfWorkFactory | None,
    actor: AdminUsersActor,
    *,
    skip: int,
    limit: int,
    sort_field: str,
    sort_order: str,
    username: str | None,
    email: str | None,
    membership_type: str | None,
    is_superuser: bool | None,
) -> UserListPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        records, total = unit_of_work.list_users(
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_order=sort_order,
            username=username,
            email=email,
            membership_type=membership_type,
            is_superuser=is_superuser,
        )
        return {"data": [user_payload(record) for record in records], "total": total}


def get_user(
    factory: UnitOfWorkFactory | None,
    actor: AdminUsersActor,
    user_id: str,
) -> UserPayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        record = unit_of_work.get_user_record(user_id)
        if record is None:
            raise AdminUserNotFound()
        return user_payload(record)


def update_user(
    factory: UnitOfWorkFactory | None,
    actor: AdminUsersActor,
    user_id: str,
    data: dict[str, Any],
) -> UserMessagePayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.load_user(user_id)
        if row is None:
            raise AdminUserNotFound()

        if row.id == actor.user_id and "is_superuser" in data:
            value = data["is_superuser"]
            if value is False or value == "false":
                raise AdminUsersBadRequest("不能取消自己的管理员权限")

        updatable = {
            "username",
            "email",
            "is_superuser",
            "membership_type",
            "membership_expires_at",
        }
        for key, value in data.items():
            if key == "membership_expires_at" and value:
                try:
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except BaseException:
                    pass
            if key in updatable and hasattr(row, key):
                unit_of_work.set_user_field(row, key, value)

        if "credits" in data and isinstance(data["credits"], (int, float)):
            credit = unit_of_work.get_user_credit(user_id)
            if credit is None:
                credit = unit_of_work.create_user_credit(user_id, 0)
            unit_of_work.set_credit_amount(credit, int(data["credits"]))

        unit_of_work.commit()
        return {"message": "用户更新成功"}


def delete_user(
    factory: UnitOfWorkFactory | None,
    actor: AdminUsersActor,
    user_id: str,
) -> UserDeletePayload:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.load_user(user_id)
        if row is None:
            raise AdminUserNotFound()
        if row.id == actor.user_id:
            raise AdminUsersBadRequest("不能删除当前登录的管理员账号")

        unit_of_work.delete_related_user_data(user_id)
        unit_of_work.delete_user(row)
        unit_of_work.commit_user_deletion()
        return {"success": True, "message": f"用户 {row.username} 已删除"}
