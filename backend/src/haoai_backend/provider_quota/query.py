"""Provider-specific quota queries behind an explicitly injected transport."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .domain import (
    DEFAULT_PROVIDER_TYPE,
    PROVIDER_SPECS,
    ProviderQuotaBalanceAccount,
    normalize_base_url,
    account_record,
    console_url_for,
)
from .ports import ProviderQuotaHttpResponse, ProviderQuotaTransport

logger = logging.getLogger(__name__)

AISTARSLAB_CREDITS_PER_CNY = 100.0
DEFAULT_QUOTA_PER_UNIT = 500000.0
DEFAULT_USD_EXCHANGE_RATE = 7.0
TTL_BY_KIND = {"bearer": 10 * 60, "session": 30 * 60}
ALIYUN_API_VERSION = "2017-12-14"
ALIYUN_DEFAULT_ENDPOINT = "https://business.aliyuncs.com"
_ALIYUN_ERROR_HINTS = {
    "InvalidAccessKeyId": "AccessKey ID 不存在或已禁用",
    "SignatureDoesNotMatch": "AccessKey Secret 不正确",
    "NoPermission": "该 AccessKey 没有费用中心读权限（给 RAM 子账号授予 AliyunBSSReadOnlyAccess 即可）",
    "NotAuthorized": "该 AccessKey 未授权查询账户余额（需 AliyunBSSReadOnlyAccess）",
    "Forbidden": "该 AccessKey 无权访问费用中心接口",
    "InvalidTimeStamp": "本机时间与阿里云相差超过 15 分钟，请校准系统时间",
    "NotApplicable": "该接口不适用于当前调用方（请确认是阿里云主站账号）",
}


class QuotaAuthError(Exception):
    """Login failure or expired credentials that permit one fresh login."""


def _parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except Exception as error:
        raise ValueError(f"响应不是合法 JSON：{text[:200]}") from error
    if not isinstance(data, dict):
        raise ValueError(f"响应结构异常：{text[:200]}")
    return data


def _header_values(response: ProviderQuotaHttpResponse, name: str) -> tuple[str, ...]:
    value = response.headers.get(name) or response.headers.get(name.lower())
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _extract_cookie(response: ProviderQuotaHttpResponse, name: str) -> str:
    value = response.cookies.get(name)
    if value:
        return value
    for raw in _header_values(response, "Set-Cookie"):
        if raw.strip().startswith(name + "="):
            return raw.split("=", 1)[1].split(";", 1)[0]
    return ""


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProviderQuotaQueryService:
    """Run legacy provider calculations without owning an implicit network client."""

    def __init__(
        self,
        transport: ProviderQuotaTransport | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_lock: asyncio.Lock | None = None

    def invalidate(self, account_id: str) -> None:
        self._sessions.pop(str(account_id), None)

    def _lock(self) -> asyncio.Lock:
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        return self._session_lock

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_seconds: float,
    ) -> ProviderQuotaHttpResponse:
        if self._transport is None:
            raise RuntimeError("供应商额度探测服务尚未接线")
        return await self._transport.request(
            method,
            url,
            headers=headers,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )

    async def _fetch_status(self, base_url: str) -> dict[str, Any]:
        result = {
            "quota_per_unit": DEFAULT_QUOTA_PER_UNIT,
            "usd_exchange_rate": DEFAULT_USD_EXCHANGE_RATE,
            "display_type": "",
            "system_name": "",
        }
        try:
            response = await self._request(
                "GET", base_url + "/api/status", timeout_seconds=8
            )
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            data = _parse_json(response.text)
            values = data.get("data") or {}
            quota_per_unit = float(values.get("quota_per_unit") or 0)
            exchange_rate = float(values.get("usd_exchange_rate") or 0)
            result.update(
                {
                    "quota_per_unit": quota_per_unit or DEFAULT_QUOTA_PER_UNIT,
                    "usd_exchange_rate": exchange_rate or DEFAULT_USD_EXCHANGE_RATE,
                    "display_type": str(
                        values.get("quota_display_type") or ""
                    ).upper(),
                    "system_name": str(values.get("system_name") or "").strip(),
                }
            )
        except Exception as error:
            logger.warning(
                "[额度监控] 获取 %s/api/status 失败，使用默认换算: %s",
                base_url,
                error,
            )
        return result

    async def _login(self, base_url: str, username: str, password: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            base_url + "/api/user/login",
            json_body={"username": username, "password": password},
            timeout_seconds=15,
        )
        if response.status >= 400:
            raise QuotaAuthError(
                f"登录失败 HTTP {response.status}：{response.text[:200]}"
            )
        data = _parse_json(response.text)
        if not data.get("success"):
            raise QuotaAuthError(
                f"登录失败：{data.get('message') or '账号或密码错误'}"
            )

        info = data.get("data") or {}
        user = info.get("user") or {}
        token = info.get("access_token") or ""
        session_value = _extract_cookie(response, "session")
        user_id = user.get("id") or info.get("id")
        if token:
            auth = {
                "kind": "bearer",
                "value": token,
                "user_id": user_id,
                "inline_user": user or None,
            }
        elif session_value:
            auth = {
                "kind": "session",
                "value": session_value,
                "user_id": user_id,
                "inline_user": user or None,
            }
        else:
            raise RuntimeError(
                "登录成功但未拿到可用凭据（供应商接口鉴权方式可能已变更）"
            )
        logger.info(
            "[额度监控] 登录成功 %s 用户 %s (id=%s, 鉴权=%s)",
            base_url,
            user.get("username") or username,
            user_id,
            auth["kind"],
        )
        return auth

    @staticmethod
    def _auth_headers(auth: Mapping[str, Any]) -> dict[str, str]:
        if auth.get("kind") == "bearer":
            return {"Authorization": "Bearer " + auth["value"]}
        headers = {"Cookie": f"session={auth['value']}"}
        if auth.get("user_id") is not None:
            headers["New-Api-User"] = str(auth["user_id"])
        return headers

    async def _fetch_self(self, base_url: str, auth: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._request(
            "GET",
            base_url + "/api/user/self",
            headers=self._auth_headers(auth),
            timeout_seconds=10,
        )
        if response.status in (401, 403):
            raise QuotaAuthError(
                f"会话已失效 HTTP {response.status}：{response.text[:200]}"
            )
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}：{response.text[:200]}")
        data = _parse_json(response.text)
        if not data.get("success"):
            message = str(data.get("message") or "")
            if "登录" in message or "权限" in message or "token" in message.lower():
                raise QuotaAuthError(f"会话已失效：{message}")
            raise RuntimeError(f"查询失败：{message or '未知错误'}")
        return data.get("data") or {}

    async def _resolve_user_info(
        self,
        account_key: str,
        base_url: str,
        username: str,
        password: str,
    ) -> tuple[dict[str, Any], bool]:
        key = account_key or f"{base_url}|{username}"
        cached = self._sessions.get(key)
        if cached and cached.get("expires_at", 0) > self._clock():
            try:
                return await self._fetch_self(base_url, cached), True
            except QuotaAuthError as error:
                logger.info("[额度监控] 缓存会话失效，改为重新登录：%s", error)
                self._sessions.pop(key, None)

        async with self._lock():
            fresh = self._sessions.get(key)
            if fresh and fresh is not cached and fresh.get("expires_at", 0) > self._clock():
                auth = fresh
            else:
                auth = await self._login(base_url, username, password)
                auth["expires_at"] = self._clock() + TTL_BY_KIND.get(
                    auth["kind"], 10 * 60
                )
                self._sessions[key] = auth

        inline_user = auth.get("inline_user")
        if inline_user and inline_user.get("quota") is not None:
            return inline_user, False
        return await self._fetch_self(base_url, auth), False

    @staticmethod
    def _base_result(
        account_id: str,
        provider_type: str,
        name: str,
        base_url: str,
        username: str,
    ) -> dict[str, Any]:
        return {
            "id": account_id,
            "name": name or "",
            "provider_type": provider_type,
            "base_url": base_url,
            "username": username or "",
            "configured": False,
            "unit": "$",
            "unit_suffix": "",
            "decimals": 2,
            "balance_label": "剩余额度",
            "warn_below": 10,
            "console_url": "",
            "extra_meta": [],
            "credits": None,
            "display_type": "",
            "balance": None,
            "used": None,
            "total": None,
            "balance_usd": None,
            "balance_cny": None,
            "used_usd": None,
            "used_cny": None,
            "granted": None,
            "topped_up": None,
            "available": None,
            "quota": None,
            "used_quota": None,
            "quota_per_unit": DEFAULT_QUOTA_PER_UNIT,
            "usd_exchange_rate": DEFAULT_USD_EXCHANGE_RATE,
            "system_name": "",
            "session_reused": False,
            "errors": [],
            "updated_at": _now_str(),
        }

    async def get_balance(
        self,
        *,
        account_id: str = "",
        provider_type: str = DEFAULT_PROVIDER_TYPE,
        name: str = "",
        base_url: str = "",
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        base = normalize_base_url(base_url)
        result = self._base_result(account_id, provider_type, name, base, username)
        spec = PROVIDER_SPECS.get(provider_type)
        if not spec:
            result["errors"].append(
                f"暂不支持的供应商架构：{provider_type or '未填写'}"
            )
            return result
        if not base:
            result["errors"].append("未填写网关地址")
            return result

        result.update(
            {
                "warn_below": spec.get("warn_below", 10),
                "decimals": spec.get("decimals", 2),
                "unit_suffix": spec.get("unit_suffix", ""),
                "balance_label": spec.get("balance_label", "剩余额度"),
                "console_url": console_url_for(provider_type, base),
            }
        )
        result["configured"] = bool(password) and (
            bool(username) or not spec["need_username"]
        )
        if not result["configured"]:
            result["errors"].append(
                "未配置登录账号与密码"
                if spec["need_username"]
                else "未配置 API Key"
            )
            return result

        if provider_type == "deepseek":
            return await self._query_deepseek(result, base, password)
        if provider_type == "aistarslab":
            return await self._query_aistarslab(result, base, password)
        if provider_type == "aliyun":
            return await self._query_aliyun(result, base, username, password)
        return await self._query_new_api(result, account_id, base, username, password)

    async def _query_new_api(
        self,
        result: dict[str, Any],
        account_id: str,
        base: str,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        status = await self._fetch_status(base)
        quota_per_unit = status["quota_per_unit"]
        exchange_rate = status["usd_exchange_rate"]
        display_type = status["display_type"]
        result.update(
            {
                "quota_per_unit": quota_per_unit,
                "usd_exchange_rate": exchange_rate,
                "display_type": display_type,
                "unit": "¥" if display_type == "CNY" else "$",
                "system_name": status["system_name"],
            }
        )
        if not result["name"]:
            result["name"] = status["system_name"] or urlparse(base).netloc or base

        try:
            info, reused = await self._resolve_user_info(
                account_id, base, username, password
            )
        except Exception as error:
            logger.warning("[额度监控] %s 查询失败: %s", base, error)
            result["errors"].append(f"额度查询失败：{error}")
            return result

        try:
            quota = float(info.get("quota") or 0)
            used_quota = float(info.get("used_quota") or 0)
        except Exception:
            result["errors"].append(
                "响应中的 quota / used_quota 不是数字，供应商接口可能已变更"
            )
            return result

        balance_usd = quota / quota_per_unit
        used_usd = used_quota / quota_per_unit
        factor = exchange_rate if display_type == "CNY" else 1.0
        result.update(
            {
                "quota": quota,
                "used_quota": used_quota,
                "balance_usd": round(balance_usd, 2),
                "balance_cny": round(balance_usd * exchange_rate, 2),
                "used_usd": round(used_usd, 2),
                "used_cny": round(used_usd * exchange_rate, 2),
                "balance": round(balance_usd * factor, 2),
                "used": round(used_usd * factor, 2),
                "total": round((balance_usd + used_usd) * factor, 2),
                "session_reused": reused,
            }
        )
        return result

    async def _fetch_deepseek_balance(
        self,
        base_url: str,
        api_key: str,
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            base_url + "/user/balance",
            headers={
                "Authorization": "Bearer " + api_key,
                "Accept": "application/json",
            },
            timeout_seconds=10,
        )
        if response.status in (401, 403):
            raise QuotaAuthError(
                f"API Key 无效或已过期（HTTP {response.status}）"
            )
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}：{response.text[:200]}")
        return _parse_json(response.text)

    async def _fetch_aistarslab_credits(
        self,
        base_url: str,
        api_key: str,
    ) -> Any:
        response = await self._request(
            "GET",
            base_url.rstrip("/") + "/account/credits",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout_seconds=30,
        )
        # Preserve the legacy service's raw JSON behavior: only invalid JSON is
        # converted to an error payload; valid non-object JSON reaches `.get()`.
        try:
            return json.loads(response.text)
        except Exception:
            logger.error("[AIStarsLab] 非 JSON 响应: %s", response.text[:300])
            return {"code": -1, "msg": "响应解析失败", "data": None}

    async def _query_deepseek(
        self,
        result: dict[str, Any],
        base: str,
        api_key: str,
    ) -> dict[str, Any]:
        if not result["name"]:
            result["name"] = "DeepSeek"
        try:
            data = await self._fetch_deepseek_balance(base, api_key)
        except Exception as error:
            logger.warning("[额度监控] DeepSeek %s 查询失败: %s", base, error)
            result["errors"].append(f"额度查询失败：{error}")
            return result

        info = self._pick_balance_info(data.get("balance_infos"))
        if not info:
            result["errors"].append("接口未返回余额信息（balance_infos 为空）")
            return result

        currency = str(info.get("currency") or "").upper()
        total = self._to_float(info.get("total_balance"))
        granted = self._to_float(info.get("granted_balance"))
        topped_up = self._to_float(info.get("topped_up_balance"))
        if total is None and (granted is not None or topped_up is not None):
            total = (granted or 0) + (topped_up or 0)
        if total is None:
            result["errors"].append(
                "响应里没有可用的余额字段（total_balance / granted_balance / topped_up_balance 均为空）"
            )
            return result

        available = data.get("is_available")
        result.update(
            {
                "unit": "¥"
                if currency == "CNY"
                else "$"
                if currency == "USD"
                else currency or "¥",
                "display_type": currency,
                "balance": round(total, 2),
                "granted": round(granted, 2) if granted is not None else None,
                "topped_up": round(topped_up, 2) if topped_up is not None else None,
                "available": bool(available) if available is not None else None,
                "used": None,
                "total": None,
            }
        )
        return result

    async def _query_aistarslab(
        self,
        result: dict[str, Any],
        base: str,
        api_key: str,
    ) -> dict[str, Any]:
        if not result["name"]:
            result["name"] = "AIStarsLab"
        try:
            data = await self._fetch_aistarslab_credits(base, api_key)
            if data.get("code") != 0:
                raise RuntimeError(f"AIStarsLab 查询积分失败: {data.get('msg')}")
            credits = (data.get("data") or {}).get("credits")
        except Exception as error:
            logger.warning("[额度监控] AIStarsLab %s 查询失败: %s", base, error)
            result["errors"].append(f"额度查询失败：{error}")
            return result

        if credits is None:
            result["errors"].append("接口未返回积分字段（data.credits 为空）")
            return result

        credits = float(credits)
        result.update(
            {
                "unit": "¥",
                "balance": round(credits / AISTARSLAB_CREDITS_PER_CNY, 2),
                "credits": credits,
                "used": None,
                "total": None,
            }
        )
        return result

    async def _query_aliyun(
        self,
        result: dict[str, Any],
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> dict[str, Any]:
        if not result["name"]:
            result["name"] = "阿里云"
        try:
            data = await self._fetch_aliyun_balance(
                endpoint or ALIYUN_DEFAULT_ENDPOINT,
                access_key_id,
                access_key_secret,
            )
        except Exception as error:
            logger.warning("[额度监控] 阿里云 %s 查询失败: %s", endpoint, error)
            result["errors"].append(f"额度查询失败：{error}")
            return result

        values = data.get("Data") or {}
        amount = self._to_float(values.get("AvailableAmount"))
        if amount is None:
            result["errors"].append("接口未返回可用额度（Data.AvailableAmount 为空）")
            return result

        currency = str(values.get("Currency") or "CNY").upper()
        unit = "¥" if currency == "CNY" else "$" if currency == "USD" else currency
        cash = self._to_float(values.get("AvailableCashAmount"))
        credit = self._to_float(values.get("CreditAmount"))
        extra = []
        if cash is not None:
            extra.append(f"现金 {unit}{cash:.2f}")
        if credit:
            extra.append(f"信控 {unit}{credit:.2f}")
        result.update(
            {
                "unit": unit,
                "display_type": currency,
                "balance": round(amount, 2),
                "available": amount > 0,
                "extra_meta": extra,
                "used": None,
                "total": None,
            }
        )
        return result

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _pick_balance_info(infos: Any) -> dict[str, Any] | None:
        if not isinstance(infos, list) or not infos:
            return None
        for item in infos:
            if (
                isinstance(item, dict)
                and str(item.get("currency") or "").upper() == "CNY"
            ):
                return item
        return infos[0] if isinstance(infos[0], dict) else None

    @staticmethod
    def _aliyun_percent_encode(value: Any) -> str:
        return (
            quote(str(value), safe="")
            .replace("+", "%20")
            .replace("*", "%2A")
            .replace("%7E", "~")
        )

    @classmethod
    def build_aliyun_signed_url(
        cls,
        endpoint: str,
        action: str,
        access_key_id: str,
        access_key_secret: str,
        *,
        timestamp: datetime | None = None,
        nonce: str | None = None,
    ) -> str:
        params = {
            "Action": action,
            "Version": ALIYUN_API_VERSION,
            "Format": "JSON",
            "AccessKeyId": access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": nonce or str(uuid.uuid4()),
            "Timestamp": (timestamp or datetime.utcnow()).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        canonical = "&".join(
            f"{cls._aliyun_percent_encode(key)}={cls._aliyun_percent_encode(value)}"
            for key, value in sorted(params.items())
        )
        string_to_sign = "GET&%2F&" + cls._aliyun_percent_encode(canonical)
        digest = hmac.new(
            (access_key_secret + "&").encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        )
        signature = base64.b64encode(digest.digest()).decode()
        root = (endpoint or ALIYUN_DEFAULT_ENDPOINT).rstrip("/")
        return (
            root
            + "/?Signature="
            + cls._aliyun_percent_encode(signature)
            + "&"
            + canonical
        )

    async def _fetch_aliyun_balance(
        self,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> dict[str, Any]:
        url = self.build_aliyun_signed_url(
            endpoint,
            "QueryAccountBalance",
            access_key_id,
            access_key_secret,
        )
        response = await self._request(
            "GET", url, timeout_seconds=10
        )
        try:
            data = _parse_json(response.text)
        except Exception as error:
            raise RuntimeError(f"HTTP {response.status}：{response.text[:200]}") from error
        if response.status >= 400 or not data.get("Success"):
            code = str(data.get("Code") or f"HTTP {response.status}")
            message = _ALIYUN_ERROR_HINTS.get(code) or str(data.get("Message") or "")
            if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
                raise QuotaAuthError(f"{code}：{message}")
            raise RuntimeError(f"{code}：{message}")
        return data

    async def probe(
        self,
        base_url: str,
        username: str,
        password: str,
        provider_type: str = DEFAULT_PROVIDER_TYPE,
    ) -> dict[str, Any]:
        result = await self.get_balance(
            account_id="",
            provider_type=provider_type,
            name="",
            base_url=base_url,
            username=username,
            password=password,
        )
        result["ok"] = bool(not result["errors"] and result["balance"] is not None)
        return result

    async def account_balance(
        self,
        prepared: ProviderQuotaBalanceAccount,
    ) -> dict[str, Any]:
        account = account_record(prepared.account)
        if not account.is_active:
            return {
                "id": account.id,
                "name": account.name or "",
                "provider_type": account.provider_type,
                "base_url": account.base_url or "",
                "is_active": False,
                "configured": bool(prepared.username and prepared.password),
                "disabled": True,
                "unit": "$",
                "balance": None,
                "used": None,
                "total": None,
                "system_name": "",
                "errors": [],
                "updated_at": "",
                "session_reused": False,
                "console_url": console_url_for(
                    account.provider_type or "", account.base_url or ""
                ),
            }
        try:
            result = await self.get_balance(
                account_id=account.id,
                provider_type=account.provider_type or DEFAULT_PROVIDER_TYPE,
                name=account.name or "",
                base_url=account.base_url or "",
                username=prepared.username,
                password=prepared.password,
            )
        except Exception as error:
            logger.error(
                "[额度监控] 查询 %s 异常: %s",
                account.id,
                error,
                exc_info=True,
            )
            result = {
                "id": account.id,
                "name": account.name or "",
                "provider_type": account.provider_type,
                "base_url": account.base_url or "",
                "configured": True,
                "unit": "$",
                "balance": None,
                "used": None,
                "total": None,
                "system_name": "",
                "errors": [f"额度查询失败：{error}"],
                "updated_at": "",
                "session_reused": False,
                "console_url": console_url_for(
                    account.provider_type or "", account.base_url or ""
                ),
            }
        result["is_active"] = True
        return result
