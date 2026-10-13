"""SMTP-account use cases preserving the legacy transaction boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .domain import (
    AdminSmtpAccountsActor,
    SmtpAccountRecord,
    account_created_payload,
    account_payload,
)
from .errors import (
    AdminSmtpAccountInputInvalid,
    AdminSmtpAccountNotFound,
    AdminSmtpAccountsForbidden,
    AdminSmtpAccountsUnavailable,
)
from .ports import Cipher, DailyReset, UnitOfWorkFactory, AdminSmtpAccountUnitOfWork

_UPDATABLE_FIELDS = {"host", "port", "user", "is_active"}


@contextmanager
def _open(factory: UnitOfWorkFactory | None) -> Iterator[AdminSmtpAccountUnitOfWork]:
    if factory is None:
        raise AdminSmtpAccountsUnavailable()
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


def _require_admin(actor: AdminSmtpAccountsActor) -> None:
    if not actor.is_superuser:
        raise AdminSmtpAccountsForbidden()


def _payload(row: Any, cipher: Cipher) -> dict[str, Any]:
    if isinstance(row, SmtpAccountRecord):
        account = row
    else:
        account = SmtpAccountRecord(
            id=row.id,
            host=row.host,
            port=row.port,
            user=row.user,
            password=row.password,
            daily_sent=row.daily_sent,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    return account_payload(account, cipher.decrypt(account.password))


def list_smtp_accounts(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    cipher: Cipher,
    daily_reset: DailyReset,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        daily_reset.reset_if_needed()
        accounts = unit_of_work.list_accounts()
        return {"data": [_payload(account, cipher) for account in accounts], "total": len(accounts)}


def get_smtp_account(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    account_id: str,
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        account = unit_of_work.get_account(account_id)
        if account is None:
            raise AdminSmtpAccountNotFound()
        return _payload(account, cipher)


def create_smtp_account(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    data: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        if not data.get("user") or not data.get("password"):
            raise AdminSmtpAccountInputInvalid()
        account_id = str(uuid4())
        host = data.get("host", "smtp.qq.com")
        port = int(data.get("port", 465))
        user = data["user"]
        password = cipher.encrypt(str(data["password"]))
        values = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "daily_sent": 0,
            "is_active": 1 if data.get("is_active", True) else 0,
        }
        row = unit_of_work.add_account(account_id, values)
        unit_of_work.commit()
        refreshed = unit_of_work.refresh(row)
        return account_created_payload(account_id, refreshed.user)


def update_smtp_account(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    account_id: str,
    data: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        account = unit_of_work.get_account(account_id)
        if account is None:
            raise AdminSmtpAccountNotFound()
        for key, value in data.items():
            if key == "password":
                if value and "****" not in value:
                    value = cipher.encrypt(str(value))
                else:
                    continue
            if key == "port" and value:
                value = int(value)
            if key == "is_active":
                value = 1 if value else 0
            if key in _UPDATABLE_FIELDS or key == "password":
                unit_of_work.set_account_value(account, key, value)
        unit_of_work.commit()
        return {"message": "更新成功"}


def delete_smtp_account(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    account_id: str,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        account = unit_of_work.get_account(account_id)
        if account is None:
            raise AdminSmtpAccountNotFound()
        unit_of_work.delete_account(account)
        unit_of_work.commit()
        return {"message": "删除成功"}


def reset_smtp_count(
    factory: UnitOfWorkFactory | None,
    actor: AdminSmtpAccountsActor,
    account_id: str,
) -> dict[str, str]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        account = unit_of_work.get_account(account_id)
        if account is None:
            raise AdminSmtpAccountNotFound()
        unit_of_work.set_account_value(account, "daily_sent", 0)
        unit_of_work.commit()
        return {"message": "已重置"}
