"""Provider connection checks with an injectable synchronous transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domain import ConnectionConfig
from .ports import ConnectionTransport


class UrllibConnectionTransport:
    """Legacy GET transport; an opener can be injected for deterministic tests."""

    def __init__(
        self,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._opener = urlopen if opener is None else opener

    def get(self, url: str, headers: Mapping[str, str], *, timeout: int) -> int:
        request = Request(url, headers=dict(headers), method="GET")
        with self._opener(request, timeout=timeout) as response:
            return response.status


class ConnectionTester:
    """Apply the fixed legacy provider URL and error-mapping rules."""

    timeout_seconds = 15

    def __init__(self, transport: ConnectionTransport) -> None:
        self._transport = transport

    def test(self, config: ConnectionConfig) -> dict[str, object]:
        if not config.api_key:
            return {"success": False, "message": "API Key 不能为空"}

        url, headers = self._target(config)
        if not url:
            return {"success": False, "message": "请配置 Base URL"}

        try:
            status_code = self._transport.get(
                url,
                headers,
                timeout=self.timeout_seconds,
            )
            if status_code == 200:
                return {
                    "success": True,
                    "message": "连接成功！API 配置正确。",
                    "status_code": status_code,
                }
            return {
                "success": False,
                "message": f"连接失败：HTTP {status_code}",
                "status_code": status_code,
            }
        except HTTPError as error:
            status_code = error.code
            if status_code == 401:
                message = "认证失败：API Key 无效或已过期"
            elif status_code == 403:
                message = "权限不足：API Key 没有访问权限"
            elif status_code == 404:
                message = "端点不存在：请检查 Base URL 是否正确 (HTTP 404)"
            else:
                message = f"连接失败：HTTP {status_code}"
            return {
                "success": False,
                "message": message,
                "status_code": status_code,
            }
        except URLError as error:
            return {
                "success": False,
                "message": f"无法连接到服务器：{error.reason}",
            }
        except Exception as error:
            error_message = str(error) or repr(error) or type(error).__name__
            return {
                "success": False,
                "message": f"测试失败：{error_message[:200]}",
            }

    @staticmethod
    def _target(config: ConnectionConfig) -> tuple[str | None, dict[str, str]]:
        provider = config.provider
        base_url = config.base_url
        if config.category == "text":
            if provider == "openai":
                url = f"{base_url or 'https://api.openai.com/v1'}/models"
                headers = {"Authorization": f"Bearer {config.api_key}"}
            elif provider == "anthropic":
                url = f"{base_url or 'https://api.anthropic.com/v1'}/messages"
                headers = {
                    "x-api-key": config.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
            elif provider == "google":
                url = (
                    f"{base_url or 'https://generativelanguage.googleapis.com/v1'}"
                    "/models"
                )
                headers = {"x-goog-api-key": config.api_key}
            else:
                url = f"{base_url}/models" if base_url else None
                headers = {"Authorization": f"Bearer {config.api_key}"}
        elif config.category in {"image", "video"}:
            url = f"{base_url}/models" if base_url else None
            headers = {"Authorization": f"Bearer {config.api_key}"}
        else:
            url = f"{base_url}/models" if base_url else None
            headers = {"Authorization": f"Bearer {config.api_key}"}
        return url, headers
