from __future__ import annotations

import asyncio
from typing import Any

from haoai_backend.zhangyuge.application import get_balance, normalize_base_url
from haoai_backend.zhangyuge.domain import BalanceCredentials


class FakeBalanceTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str] | None, float]] = []

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 8,
    ) -> Any:
        self.requests.append((url, headers, timeout_seconds))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def test_normalize_base_url_removes_only_trailing_v1() -> None:
    assert normalize_base_url("") == "https://zexapi.com"
    assert normalize_base_url(" https://gateway.example/v1/// ") == "https://gateway.example"
    assert normalize_base_url("https://gateway.example/api") == "https://gateway.example/api"


def test_balance_uses_pat_and_api_key_and_keeps_legacy_response_shape() -> None:
    base = "https://gateway.example"
    transport = FakeBalanceTransport(
        {
            f"{base}/api/status": {
                "data": {"quota_per_unit": 1000, "usd_exchange_rate": 2},
            },
            f"{base}/api/user/self": {"data": {"quota": 2500}},
            f"{base}/v1/dashboard/billing/usage": {"total_usage": 345},
        }
    )

    result = asyncio.run(
        get_balance(
            BalanceCredentials(
                api_key="fake-api-key",
                base_url=f"{base}/v1/",
                pat="fake-pat",
            ),
            transport,
        )
    )

    assert {
        key: value for key, value in result.items() if key != "updated_at"
    } == {
        "balance_usd": 2.5,
        "balance_cny": 5.0,
        "used_usd": 3.45,
        "used_cny": 6.9,
        "quota_per_unit": 1000.0,
        "usd_exchange_rate": 2.0,
        "pat_configured": True,
        "api_key_configured": True,
        "errors": [],
    }
    assert len(result["updated_at"]) == 19
    assert [request[0] for request in transport.requests] == [
        f"{base}/api/status",
        f"{base}/api/user/self",
        f"{base}/v1/dashboard/billing/usage",
    ]
    assert transport.requests[0][1] is None
    assert transport.requests[1][1] == {"Authorization": "Bearer fake-pat"}
    assert transport.requests[2][1] == {"Authorization": "Bearer fake-api-key"}
    assert all(request[2] == 8 for request in transport.requests)


def test_balance_falls_back_for_status_and_collects_independent_errors() -> None:
    base = "https://gateway.example"
    transport = FakeBalanceTransport(
        {
            f"{base}/api/status": RuntimeError("status unavailable"),
            f"{base}/api/user/self": RuntimeError("HTTP 401: fake PAT rejected"),
            f"{base}/v1/dashboard/billing/usage": {"total_usage": "bad"},
        }
    )

    result = asyncio.run(
        get_balance(
            BalanceCredentials(
                api_key="fake-api-key",
                base_url=base,
                pat="fake-pat",
            ),
            transport,
        )
    )

    assert result["quota_per_unit"] == 500000.0
    assert result["usd_exchange_rate"] == 7.0
    assert result["balance_usd"] is None
    assert result["used_usd"] is None
    assert result["errors"] == [
        "剩余余额查询失败（PAT 无效或已过期？）：HTTP 401: fake PAT rejected",
        "已用额度查询失败（API Key 无效？）：could not convert string to float: 'bad'",
    ]
