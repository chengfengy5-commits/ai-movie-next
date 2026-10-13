"""Provider-quota CRUD use cases and legacy request validation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from .domain import (
    DEFAULT_PROVIDER_TYPE,
    PROVIDER_SPECS,
    ProviderQuotaActor,
    ProviderQuotaBalanceAccount,
    account_label,
    account_record,
    normalize_base_url,
    serialize_account,
)
from .errors import (
    ProviderQuotaForbidden,
    ProviderQuotaInputInvalid,
    ProviderQuotaNotFound,
    ProviderQuotaUnavailable,
)
from .ports import Cipher, ProviderQuotaUnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


@contextmanager
def _open(factory: UnitOfWorkFactory | None) -> Iterator[ProviderQuotaUnitOfWork]:
    if factory is None:
        raise ProviderQuotaUnavailable()
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


def _require_admin(actor: ProviderQuotaActor) -> None:
    if not actor.is_superuser:
        raise ProviderQuotaForbidden()


def decrypt_credentials(account: Any, cipher: Cipher) -> tuple[str, str]:
    """Return both legacy credentials, swallowing each decryption failure."""
    try:
        username = cipher.decrypt(account.username or "") or ""
    except Exception as error:
        logger.warning("[额度监控] 账号解密失败 %s: %s", account.id, error)
        username = ""
    try:
        password = cipher.decrypt(account.password or "") or ""
    except Exception as error:
        logger.warning("[额度监控] 密码解密失败 %s: %s", account.id, error)
        password = ""
    return username, password


def _serialize(account: Any, cipher: Cipher) -> dict[str, Any]:
    username, _password = decrypt_credentials(account, cipher)
    return serialize_account(account, username=username)


def validate_account_payload(
    payload: Mapping[str, Any],
    *,
    is_update: bool = False,
) -> str:
    provider_type = payload.get("provider_type") or DEFAULT_PROVIDER_TYPE
    spec = PROVIDER_SPECS.get(provider_type)
    if not spec:
        raise ProviderQuotaInputInvalid(
            f"暂不支持的供应商架构：{payload.get('provider_type')}"
        )

    base_url = normalize_base_url(payload.get("base_url") or "")
    if not base_url:
        raise ProviderQuotaInputInvalid("请填写网关地址")

    if not is_update:
        credentials_label = spec.get("credentials_label", "登录账号与密码")
        if not (payload.get("password") or "").strip():
            raise ProviderQuotaInputInvalid(f"请填写{credentials_label}")
        if spec["need_username"] and not (payload.get("username") or "").strip():
            raise ProviderQuotaInputInvalid(f"请填写{credentials_label}")
    return base_url


def validate_probe_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    provider_type = payload.get("provider_type") or DEFAULT_PROVIDER_TYPE
    spec = PROVIDER_SPECS.get(provider_type)
    if not spec:
        raise ProviderQuotaInputInvalid(
            f"暂不支持的供应商架构：{payload.get('provider_type')}"
        )

    base_url = normalize_base_url(payload.get("base_url") or "")
    if not base_url:
        raise ProviderQuotaInputInvalid("请填写网关地址")
    credentials_label = spec.get("credentials_label", "登录账号与密码")
    if not (payload.get("password") or "").strip():
        raise ProviderQuotaInputInvalid(f"请填写{credentials_label}")
    if spec["need_username"] and not (payload.get("username") or "").strip():
        raise ProviderQuotaInputInvalid(f"请填写{credentials_label}")
    return base_url, provider_type


def list_accounts(
    factory: UnitOfWorkFactory | None,
    actor: ProviderQuotaActor,
    cipher: Cipher,
) -> list[dict[str, Any]]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        return [_serialize(row, cipher) for row in unit_of_work.list_accounts()]


def create_account(
    factory: UnitOfWorkFactory | None,
    actor: ProviderQuotaActor,
    payload: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    base_url = validate_account_payload(payload)
    provider_type = (payload.get("provider_type") or DEFAULT_PROVIDER_TYPE).strip()
    with _open(factory) as unit_of_work:
        account_id = str(uuid4())
        row = unit_of_work.add_account(
            account_id,
            {
                "name": (payload.get("name") or "").strip()[:100],
                "provider_type": provider_type or DEFAULT_PROVIDER_TYPE,
                "base_url": base_url,
                "username": cipher.encrypt((payload.get("username") or "").strip()),
                "password": cipher.encrypt((payload.get("password") or "").strip()),
                "is_active": 1 if payload.get("is_active", True) else 0,
            },
        )
        unit_of_work.commit()
        refreshed = unit_of_work.refresh(row)
        logger.info(
            "[额度监控] 新建监控账号 %s (%s)",
            account_label(refreshed),
            account_id,
        )
        return _serialize(refreshed, cipher)


def update_account(
    factory: UnitOfWorkFactory | None,
    actor: ProviderQuotaActor,
    account_id: str,
    payload: Mapping[str, Any],
    cipher: Cipher,
) -> dict[str, Any]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.get_account(account_id)
        if row is None:
            raise ProviderQuotaNotFound()
        base_url = validate_account_payload(payload, is_update=True)
        provider_type = payload.get("provider_type") or row.provider_type
        values = {
            "name": (payload.get("name") or "").strip()[:100],
            "provider_type": provider_type.strip() if provider_type else provider_type,
            "base_url": base_url,
            "is_active": 1 if payload.get("is_active", True) else 0,
        }
        username = (payload.get("username") or "").strip()
        password = (payload.get("password") or "").strip()
        if username:
            values["username"] = cipher.encrypt(username)
        if password:
            values["password"] = cipher.encrypt(password)
        for field, value in values.items():
            unit_of_work.set_account_value(row, field, value)
        unit_of_work.commit()
        refreshed = unit_of_work.refresh(row)
        logger.info(
            "[额度监控] 修改监控账号 %s (%s)",
            account_label(refreshed),
            account_id,
        )
        return _serialize(refreshed, cipher)


def delete_account(
    factory: UnitOfWorkFactory | None,
    actor: ProviderQuotaActor,
    account_id: str,
    *,
    invalidate_sessions: Callable[[str], None] | None = None,
) -> dict[str, bool]:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.get_account(account_id)
        if row is None:
            raise ProviderQuotaNotFound()
        label = account_label(row)
        unit_of_work.delete_account(row)
        unit_of_work.commit()
        if invalidate_sessions is not None:
            invalidate_sessions(str(row.id))
        logger.info("[额度监控] 删除监控账号 %s (%s)", label, account_id)
        return {"ok": True}


def load_balance_account(
    factory: UnitOfWorkFactory | None,
    actor: ProviderQuotaActor,
    account_id: str,
    cipher: Cipher,
) -> ProviderQuotaBalanceAccount:
    _require_admin(actor)
    with _open(factory) as unit_of_work:
        row = unit_of_work.get_account(account_id)
        if row is None:
            raise ProviderQuotaNotFound()
        username, password = decrypt_credentials(row, cipher)
        return ProviderQuotaBalanceAccount(
            account=account_record(row),
            username=username,
            password=password,
        )
