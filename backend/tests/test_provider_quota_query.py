from __future__ import annotations

import asyncio
from datetime import datetime

from haoai_backend.provider_quota.domain import (
    ProviderQuotaAccountRecord,
    ProviderQuotaBalanceAccount,
)
from haoai_backend.provider_quota.query import ProviderQuotaQueryService
from test_provider_quota_support import FakeProviderQuotaTransport


def run(awaitable):
    return asyncio.run(awaitable)


def test_new_api_cookie_login_conversion_and_session_reuse():
    transport = FakeProviderQuotaTransport()
    service = ProviderQuotaQueryService(transport, clock=lambda: 100.0)
    base = "https://gateway.example"
    status_url = base + "/api/status"
    login_url = base + "/api/user/login"
    self_url = base + "/api/user/self"

    transport.enqueue(
        "GET",
        status_url,
        payload={
            "data": {
                "quota_per_unit": 1000,
                "usd_exchange_rate": 7,
                "quota_display_type": "CNY",
                "system_name": "Gateway",
            }
        },
    )
    transport.enqueue(
        "POST",
        login_url,
        payload={"success": True, "data": {"id": 17}},
        headers={"Set-Cookie": ("session=fake-session; Path=/",)},
    )
    transport.enqueue(
        "GET",
        self_url,
        payload={"success": True, "data": {"quota": 3000, "used_quota": 1000}},
    )
    first = run(
        service.get_balance(
            account_id="account-1",
            provider_type="new_api",
            name="",
            base_url=base + "/v1/",
            username="operator",
            password="fake-password",
        )
    )
    assert first["base_url"] == base
    assert first["name"] == "Gateway"
    assert first["balance"] == 21
    assert first["used"] == 7
    assert first["total"] == 28
    assert first["balance_usd"] == 3
    assert first["balance_cny"] == 21
    assert first["session_reused"] is False
    assert first["username"] == "operator"
    assert "password" not in first
    self_call = transport.calls[2]
    assert self_call[2] == {
        "Cookie": "session=fake-session",
        "New-Api-User": "17",
    }
    assert transport.calls[1][3] == {
        "username": "operator",
        "password": "fake-password",
    }
    assert [call[4] for call in transport.calls] == [8, 15, 10]

    transport.enqueue("GET", status_url, payload={"data": {}})
    transport.enqueue(
        "GET",
        self_url,
        payload={"success": True, "data": {"quota": 1000, "used_quota": 0}},
    )
    second = run(
        service.get_balance(
            account_id="account-1",
            provider_type="new_api",
            base_url=base,
            username="operator",
            password="fake-password",
        )
    )
    assert second["session_reused"] is True
    assert second["quota_per_unit"] == 500000
    assert len([call for call in transport.calls if call[1] == login_url]) == 1


def test_bearer_login_inline_quota_avoids_self_request():
    transport = FakeProviderQuotaTransport()
    base = "https://new-api.example"
    transport.enqueue("GET", base + "/api/status", payload={"data": {}})
    transport.enqueue(
        "POST",
        base + "/api/user/login",
        payload={
            "success": True,
            "data": {
                "access_token": "fake-token",
                "user": {"id": "user-2", "username": "operator", "quota": 2500, "used_quota": 500},
            },
        },
    )
    result = run(
        ProviderQuotaQueryService(transport).get_balance(
            account_id="account-2",
            provider_type="new_api",
            base_url=base,
            username="operator",
            password="fake-password",
        )
    )
    assert result["quota"] == 2500 and result["used_quota"] == 500
    assert result["session_reused"] is False
    assert len(transport.calls) == 2


def test_expired_cached_auth_relogs_once_and_uses_inline_quota():
    transport = FakeProviderQuotaTransport()
    service = ProviderQuotaQueryService(transport, clock=lambda: 100.0)
    base = "https://gateway-refresh.example"
    status_url = base + "/api/status"
    login_url = base + "/api/user/login"
    self_url = base + "/api/user/self"
    transport.enqueue("GET", status_url, payload={"data": {}})
    transport.enqueue(
        "POST",
        login_url,
        payload={
            "success": True,
            "data": {
                "access_token": "first-token",
                "user": {"id": 1, "quota": 1000, "used_quota": 0},
            },
        },
    )
    first = run(
        service.get_balance(
            account_id="refresh-account",
            provider_type="new_api",
            base_url=base,
            username="operator",
            password="fake-password",
        )
    )
    assert first["balance"] == 0.0

    transport.enqueue("GET", status_url, payload={"data": {}})
    transport.enqueue("GET", self_url, status=401, payload={"message": "expired"})
    transport.enqueue(
        "POST",
        login_url,
        payload={
            "success": True,
            "data": {
                "access_token": "fresh-token",
                "user": {"id": 1, "quota": 5000, "used_quota": 1000},
            },
        },
    )
    refreshed = run(
        service.get_balance(
            account_id="refresh-account",
            provider_type="new_api",
            base_url=base,
            username="operator",
            password="fake-password",
        )
    )
    assert refreshed["balance"] == 0.01
    assert refreshed["session_reused"] is False
    assert transport.calls[3][2] == {"Authorization": "Bearer first-token"}
    assert transport.calls[4][3]["password"] == "fake-password"


def test_deepseek_prefers_cny_and_derives_total_from_components():
    transport = FakeProviderQuotaTransport()
    transport.enqueue(
        "GET",
        "https://api.deepseek.com/user/balance",
        payload={
            "balance_infos": [
                {"currency": "USD", "total_balance": "9"},
                {"currency": "CNY", "granted_balance": "20", "topped_up_balance": "80"},
            ],
            "is_available": False,
        },
    )
    result = run(
        ProviderQuotaQueryService(transport).get_balance(
            provider_type="deepseek",
            base_url="https://api.deepseek.com/",
            password="fake-api-key",
        )
    )
    assert result["balance"] == 100
    assert result["unit"] == "¥" and result["display_type"] == "CNY"
    assert result["granted"] == 20 and result["topped_up"] == 80
    assert result["available"] is False
    assert transport.calls[0][2] == {
        "Authorization": "Bearer fake-api-key",
        "Accept": "application/json",
    }
    assert transport.calls[0][4] == 10


def test_deepseek_http_auth_failure_and_aistarslab_body_code_contract():
    transport = FakeProviderQuotaTransport()
    transport.enqueue(
        "GET",
        "https://deepseek.example/user/balance",
        status=401,
        payload={"error": "invalid"},
    )
    denied = run(
        ProviderQuotaQueryService(transport).get_balance(
            provider_type="deepseek",
            base_url="https://deepseek.example",
            password="fake-api-key",
        )
    )
    assert denied["errors"] == ["额度查询失败：API Key 无效或已过期（HTTP 401）"]

    transport.enqueue(
        "GET",
        "https://aistars.example/openapi/account/credits",
        status=503,
        payload={"code": 0, "data": {"credits": 1234}},
    )
    credits = run(
        ProviderQuotaQueryService(transport).get_balance(
            provider_type="aistarslab",
            base_url="https://aistars.example/openapi",
            password="fake-api-key",
        )
    )
    assert credits["balance"] == 12.34
    assert credits["credits"] == 1234
    assert credits["unit"] == "¥"
    assert transport.calls[-1][2] == {
        "Authorization": "Bearer fake-api-key",
        "Content-Type": "application/json",
    }
    assert transport.calls[-1][4] == 30


def _aistarslab_non_object_json_result(body: str):
    transport = FakeProviderQuotaTransport()
    transport.enqueue(
        "GET",
        "https://aistars.example/openapi/account/credits",
        text=body,
    )
    return run(
        ProviderQuotaQueryService(transport).get_balance(
            provider_type="aistarslab",
            base_url="https://aistars.example/openapi",
            password="fake-api-key",
        )
    )


def test_aistarslab_array_json_preserves_legacy_diagnostic():
    result = _aistarslab_non_object_json_result("[]")

    assert result["balance"] is None
    assert result["errors"] == [
        "额度查询失败：'list' object has no attribute 'get'"
    ]


def test_aistarslab_null_json_preserves_legacy_diagnostic():
    result = _aistarslab_non_object_json_result("null")

    assert result["balance"] is None
    assert result["errors"] == [
        "额度查询失败：'NoneType' object has no attribute 'get'"
    ]


def test_aliyun_result_and_deterministic_legacy_signature(monkeypatch):
    transport = FakeProviderQuotaTransport()
    service = ProviderQuotaQueryService(transport)
    signed_url = "https://business.example/?Signature=fake&Action=QueryAccountBalance"
    monkeypatch.setattr(
        service,
        "build_aliyun_signed_url",
        lambda *_args, **_kwargs: signed_url,
    )
    transport.enqueue(
        "GET",
        signed_url,
        payload={
            "Success": True,
            "Data": {
                "AvailableAmount": "45.5",
                "AvailableCashAmount": "30",
                "CreditAmount": "15.5",
                "Currency": "CNY",
            },
        },
    )
    result = run(
        service.get_balance(
            provider_type="aliyun",
            base_url="https://business.example",
            username="fake-access-id",
            password="fake-access-secret",
        )
    )
    assert result["balance"] == 45.5
    assert result["available"] is True
    assert result["extra_meta"] == ["现金 ¥30.00", "信控 ¥15.50"]
    assert result["balance_label"] == "可用额度"

    generated = ProviderQuotaQueryService.build_aliyun_signed_url(
        "https://business.example/",
        "QueryAccountBalance",
        "fake id+*~",
        "fake-secret",
        timestamp=datetime(2026, 10, 13, 1, 2, 3),
        nonce="nonce-test",
    )
    assert generated.startswith("https://business.example/?Signature=")
    assert "AccessKeyId=fake%20id%2B%2A~" in generated
    assert "SignatureNonce=nonce-test" in generated
    assert "Timestamp=2026-10-13T01%3A02%3A03Z" in generated


def test_unwired_transport_returns_legacy_error_shape_without_requests_and_inactive_skips_it():
    service = ProviderQuotaQueryService(None)
    result = run(
        service.get_balance(
            account_id="account-x",
            provider_type="deepseek",
            base_url="https://api.deepseek.com",
            password="fake-key",
        )
    )
    assert result["configured"] is True
    assert result["errors"] == ["额度查询失败：供应商额度探测服务尚未接线"]
    probe = run(
        service.probe(
            "https://api.deepseek.com", "", "fake-key", "deepseek"
        )
    )
    assert probe["ok"] is False

    row = ProviderQuotaAccountRecord(
        id="disabled",
        name="Paused",
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        username="",
        password="cipher:key",
        is_active=0,
        sort_order=0,
        created_at=None,
        updated_at=None,
    )
    disabled = run(
        service.account_balance(
            ProviderQuotaBalanceAccount(row, "", "fake-key")
        )
    )
    assert disabled == {
        "id": "disabled",
        "name": "Paused",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
        "is_active": False,
        "configured": False,
        "disabled": True,
        "unit": "$",
        "balance": None,
        "used": None,
        "total": None,
        "system_name": "",
        "errors": [],
        "updated_at": "",
        "session_reused": False,
        "console_url": "https://platform.deepseek.com/usage",
    }
