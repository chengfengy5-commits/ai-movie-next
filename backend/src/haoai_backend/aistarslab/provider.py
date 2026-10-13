"""AIStarsLab HTTP client and the legacy process-local configuration cache."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from .ports import AistarslabTransport

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.video.aistarslab.com/openapi"
CONFIG_CACHE_TTL = 600


@dataclass(slots=True)
class ConfigCache:
    data: dict[str, Any] | None = None
    timestamp: float = 0.0

    def get_fresh(self, now: float) -> dict[str, Any] | None:
        if self.data and now - self.timestamp < CONFIG_CACHE_TTL:
            return self.data
        return None

    def store(self, data: dict[str, Any], now: float) -> None:
        self.data = data
        self.timestamp = now

    def clear(self) -> None:
        self.data = None
        self.timestamp = 0.0


class AistarslabClient:
    def __init__(self, transport: AistarslabTransport) -> None:
        self._transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        body: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        url = base_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        response_text = await self._transport.request_text(
            method,
            url,
            headers,
            json_body=body,
            timeout=timeout,
        )
        try:
            return json.loads(response_text)
        except Exception:
            logger.error("[AIStarsLab] non-JSON response: %s", response_text[:300])
            return {"code": -1, "msg": "响应解析失败", "data": None}

    async def fetch_config(
        self, api_key: str, base_url: str = DEFAULT_BASE_URL
    ) -> dict[str, Any]:
        response = await self._request(
            "GET", "/generation/config", api_key, base_url
        )
        if response.get("code") != 0:
            raise RuntimeError(
                f"AIStarsLab 拉取配置失败: {response.get('msg')}"
            )
        return response.get("data") or {}

    async def get_credits(
        self, api_key: str, base_url: str = DEFAULT_BASE_URL
    ) -> int | None:
        response = await self._request(
            "GET", "/account/credits", api_key, base_url
        )
        if response.get("code") != 0:
            raise RuntimeError(
                f"AIStarsLab 查询积分失败: {response.get('msg')}"
            )
        return (response.get("data") or {}).get("credits")


class AioHttpTransport:
    """Production transport; tests should inject a deterministic fake instead."""

    async def request_text(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        *,
        json_body: dict[str, Any] | None,
        timeout: int,
    ) -> str:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            if json_body is not None:
                async with session.post(
                    url,
                    json=json_body,
                    headers=dict(headers),
                    timeout=timeout,
                ) as response:
                    return await response.text()
            async with session.get(
                url,
                headers=dict(headers),
                timeout=timeout,
            ) as response:
                return await response.text()


def system_clock() -> float:
    return time.time()
