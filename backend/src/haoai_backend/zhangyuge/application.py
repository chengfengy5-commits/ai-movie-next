"""Zhangyuge credential use cases and legacy balance conversion rules."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from uuid import uuid4

from .domain import BalanceCredentials, ZhangyugeAdminActor
from .errors import (
    ZhangyugeCredentialsMissing,
    ZhangyugeForbidden,
    ZhangyugeUnavailable,
)
from .ports import BalanceTransport, Cipher, UnitOfWorkFactory, ZhangyugeUnitOfWork

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://zexapi.com"
DEFAULT_QUOTA_PER_UNIT = 500000.0
DEFAULT_USD_EXCHANGE_RATE = 7.0


@contextmanager
def _open(
    factory: UnitOfWorkFactory | None,
) -> Iterator[ZhangyugeUnitOfWork]:
    if factory is None:
        raise ZhangyugeUnavailable()
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


def _require_admin(actor: ZhangyugeAdminActor) -> None:
    if not actor.is_superuser:
        raise ZhangyugeForbidden()


def normalize_base_url(base_url: str = "") -> str:
    """Remove a trailing /v1 suffix from a gateway root URL."""

    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base or DEFAULT_BASE_URL


def load_balance_credentials(
    factory: UnitOfWorkFactory | None,
    actor: ZhangyugeAdminActor,
    cipher: Cipher,
) -> BalanceCredentials:
    """Read model API credentials and the account PAT using one short session."""

    _require_admin(actor)
    api_key: str | None = None
    base_url: str | None = None
    pat = ""

    with _open(factory) as unit_of_work:
        for model in unit_of_work.active_model_credentials():
            if not model.api_key:
                continue
            try:
                api_key = cipher.decrypt(model.api_key)
                base_url = model.base_url or DEFAULT_BASE_URL
                break
            except Exception as error:
                logger.warning("[章鱼哥] 解密 API Key 失败: %s", error)

        config = unit_of_work.first_system_config()
        encrypted_pat = (
            getattr(config, "zhangyuge_billing_pat", None)
            if config is not None
            else None
        )
        if encrypted_pat:
            try:
                pat = cipher.decrypt(encrypted_pat) or ""
            except Exception as error:
                logger.warning("[章鱼哥] 解密 PAT 失败: %s", error)
                pat = ""

    credentials = BalanceCredentials(
        api_key=api_key,
        base_url=base_url,
        pat=pat,
    )
    if not credentials.api_key and not credentials.pat:
        raise ZhangyugeCredentialsMissing()
    return credentials


def get_pat_configured(
    factory: UnitOfWorkFactory | None,
    actor: ZhangyugeAdminActor,
    cipher: Cipher,
) -> dict[str, bool]:
    """Return only whether the configured PAT can be read successfully."""

    _require_admin(actor)
    with _open(factory) as unit_of_work:
        config = unit_of_work.first_system_config()
        encrypted_pat = (
            getattr(config, "zhangyuge_billing_pat", None)
            if config is not None
            else None
        )
        if not encrypted_pat:
            return {"configured": False}
        try:
            pat = cipher.decrypt(encrypted_pat) or ""
        except Exception as error:
            logger.warning("[章鱼哥] 解密 PAT 失败: %s", error)
            pat = ""
        return {"configured": bool(pat)}


def save_pat(
    factory: UnitOfWorkFactory | None,
    actor: ZhangyugeAdminActor,
    pat: str,
    cipher: Cipher,
) -> dict[str, bool]:
    """Encrypt and persist a trimmed account PAT, or clear it when empty."""

    _require_admin(actor)
    with _open(factory) as unit_of_work:
        config = unit_of_work.first_system_config()
        if config is None:
            config = unit_of_work.add_system_config(str(uuid4()))
        normalized_pat = (pat or "").strip()
        ciphertext = cipher.encrypt(normalized_pat) if normalized_pat else ""
        unit_of_work.set_billing_pat(config, ciphertext)
        unit_of_work.commit()
    return {"ok": True}


async def _fetch_status(
    base_url: str,
    transport: BalanceTransport,
) -> tuple[float, float]:
    try:
        data = await transport.get_json(
            base_url + "/api/status",
            timeout_seconds=8,
        )
        details = data.get("data") or {}
        quota_per_unit = details.get("quota_per_unit") or DEFAULT_QUOTA_PER_UNIT
        exchange_rate = details.get("usd_exchange_rate") or DEFAULT_USD_EXCHANGE_RATE
        return float(quota_per_unit), float(exchange_rate)
    except Exception as error:
        logger.warning(
            "[章鱼哥] 获取 /api/status 失败，使用默认换算: %s",
            error,
        )
        return DEFAULT_QUOTA_PER_UNIT, DEFAULT_USD_EXCHANGE_RATE


async def _fetch_user_self(
    pat: str,
    base_url: str,
    transport: BalanceTransport,
) -> dict:
    data = await transport.get_json(
        base_url + "/api/user/self",
        headers={"Authorization": "Bearer " + pat},
        timeout_seconds=8,
    )
    return data.get("data") or {}


async def _fetch_usage(
    api_key: str,
    base_url: str,
    transport: BalanceTransport,
) -> float:
    data = await transport.get_json(
        base_url + "/v1/dashboard/billing/usage",
        headers={"Authorization": "Bearer " + api_key},
        timeout_seconds=8,
    )
    return float(data.get("total_usage") or 0)


async def get_balance(
    credentials: BalanceCredentials,
    transport: BalanceTransport,
) -> dict:
    """Query account balance and token usage with the legacy response shape."""

    base_url = normalize_base_url(credentials.base_url or "")
    quota_per_unit, exchange_rate = await _fetch_status(base_url, transport)
    result = {
        "balance_usd": None,
        "balance_cny": None,
        "used_usd": None,
        "used_cny": None,
        "quota_per_unit": quota_per_unit,
        "usd_exchange_rate": exchange_rate,
        "pat_configured": bool(credentials.pat),
        "api_key_configured": bool(credentials.api_key),
        "errors": [],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if credentials.pat:
        try:
            info = await _fetch_user_self(credentials.pat, base_url, transport)
            balance_usd = float(info.get("quota") or 0) / quota_per_unit
            result["balance_usd"] = round(balance_usd, 2)
            result["balance_cny"] = round(balance_usd * exchange_rate, 2)
        except Exception as error:
            result["errors"].append(
                f"剩余余额查询失败（PAT 无效或已过期？）：{error}"
            )

    if credentials.api_key:
        try:
            total_usage = await _fetch_usage(
                credentials.api_key,
                base_url,
                transport,
            )
            used_usd = total_usage / 100.0
            result["used_usd"] = round(used_usd, 2)
            result["used_cny"] = round(used_usd * exchange_rate, 2)
        except Exception as error:
            result["errors"].append(
                f"已用额度查询失败（API Key 无效？）：{error}"
            )

    return result
