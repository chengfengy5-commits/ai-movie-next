"""Provider-quota account values and legacy response projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

DEFAULT_PROVIDER_TYPE = "new_api"

# Keep these legacy fields and values together: they drive validation and the
# provider-specific labels shown by the existing quota-card UI.
PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "new_api": {
        "label": "new-api 中转站",
        "need_username": True,
        "default_base_url": "",
        "secret_label": "登录密码",
        "warn_below": 10,
        "decimals": 2,
        "unit_suffix": "",
        "balance_label": "剩余额度",
        "console_url": "",
        "credentials_label": "登录账号与密码",
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "need_username": False,
        "default_base_url": "https://api.deepseek.com",
        "secret_label": "API Key",
        "warn_below": 10,
        "decimals": 2,
        "unit_suffix": "",
        "balance_label": "剩余额度",
        "console_url": "https://platform.deepseek.com/usage",
        "credentials_label": "API Key",
    },
    "aistarslab": {
        "label": "AIStarsLab",
        "need_username": False,
        "default_base_url": "https://api.video.aistarslab.com/openapi",
        "secret_label": "API Key",
        "warn_below": 10,
        "decimals": 2,
        "unit_suffix": "",
        "balance_label": "剩余额度",
        "console_url": "",
        "credentials_label": "API Key",
    },
    "aliyun": {
        "label": "阿里云（账户余额）",
        "need_username": True,
        "default_base_url": "https://business.aliyuncs.com",
        "secret_label": "AccessKey Secret",
        "user_label": "AccessKey ID",
        "user_placeholder": "LTAI...（RAM 子账号 AccessKey ID）",
        "secret_placeholder": "RAM 子账号 AccessKey Secret",
        "warn_below": 10,
        "decimals": 2,
        "unit_suffix": "",
        "balance_label": "可用额度",
        "console_url": "https://usercenter2.aliyun.com/finance/fund-management/overview",
        "credentials_label": "AccessKey ID 与 AccessKey Secret",
    },
}
SUPPORTED_PROVIDER_TYPES = tuple(PROVIDER_SPECS)
_API_URL_SUFFIXES = ("/openapi", "/api", "/v1", "/v1beta")


@dataclass(frozen=True, slots=True)
class ProviderQuotaActor:
    user_id: str
    is_superuser: bool


@dataclass(frozen=True, slots=True)
class ProviderQuotaAccountRecord:
    id: str
    name: Any
    provider_type: Any
    base_url: Any
    username: Any
    password: Any
    is_active: Any
    sort_order: Any
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProviderQuotaBalanceAccount:
    account: ProviderQuotaAccountRecord
    username: str
    password: str


def normalize_base_url(base_url: str = "") -> str:
    """Match the legacy gateway-root normalization exactly."""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def console_url_for(provider_type: str, base_url: str) -> str:
    spec = PROVIDER_SPECS.get(provider_type) or {}
    url = str(spec.get("console_url") or "").strip() or str(base_url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    lowered = url.lower().rstrip("/")
    for suffix in _API_URL_SUFFIXES:
        if lowered.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


def account_record(row: Any) -> ProviderQuotaAccountRecord:
    if isinstance(row, ProviderQuotaAccountRecord):
        return row
    return ProviderQuotaAccountRecord(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        username=row.username,
        password=row.password,
        is_active=row.is_active,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def serialize_account(
    row: Any,
    *,
    username: str,
) -> dict[str, Any]:
    account = account_record(row)
    provider_type = account.provider_type or DEFAULT_PROVIDER_TYPE
    return {
        "id": account.id,
        "name": account.name or "",
        "provider_type": provider_type,
        "base_url": account.base_url or "",
        "username": username,
        "is_active": bool(account.is_active),
        "sort_order": account.sort_order or 0,
        "console_url": console_url_for(provider_type, account.base_url or ""),
    }


def account_label(row: Any) -> str:
    account = account_record(row)
    return account.name or account.base_url or ""
