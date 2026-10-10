"""Injected matrix for the thirteen fixed single-GET provider protocols."""

from __future__ import annotations

import asyncio

import pytest

from haoai_backend.provider_status import create_provider_status_service
from haoai_backend.provider_status.protocols import get_provider_protocol
from provider_status_support import (
    FakeConfigurationSession,
    FakeHttpClientFactory,
    FakeResponse,
    make_configuration,
    make_runtime,
)


PROTOCOL_CASES = [
    (
        "zhangyuge",
        {"status": "completed", "video": {"url": "https://video.example/zhang"}, "result_url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        True,
        {},
        "https://video.example/zhang",
    ),
    (
        "manxiaobai",
        {"status": "SUCCESS", "video": {"url": "https://video.example/manxiao"}},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {},
        "https://video.example/manxiao",
    ),
    (
        "xai",
        {"status": "done", "video": {"url": "https://video.example/xai"}},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {"Content-Type": "application/json"},
        "https://video.example/xai",
    ),
    (
        "manxueapi",
        {"status": "success", "video": {"url": "https://video.example/manxue"}, "video_url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        60,
        False,
        {"Content-Type": "application/json"},
        "https://video.example/manxue",
    ),
    (
        "geeknow",
        {"status": "COMPLETED", "content": {"video_url": "https://video.example/geeknow"}, "url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {},
        "https://video.example/geeknow",
    ),
    (
        "snumom",
        {"status": "completed", "video_url": "https://video.example/snumom", "url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {},
        "https://video.example/snumom",
    ),
    (
        "biglongxia",
        {"status": "completed", "url": "https://video.example/biglongxia", "video_url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {},
        "https://video.example/biglongxia",
    ),
    (
        "heima",
        {"data": {"status": "COMPLETED", "url": "https://ignored.example/video"}},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {},
        "https://custom.example/v1/videos/external-123/content",
    ),
    (
        "yu25",
        {"status": "COMPLETE", "result": {"video_url": "https://video.example/yu25"}, "url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {"Accept": "application/json"},
        "https://video.example/yu25",
    ),
    (
        "yu25_beiyong",
        {"data": {"data": {"status": " READY ", "video_url": "https://video.example/yu25-backup"}}},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {"Accept": "application/json"},
        "https://video.example/yu25-backup",
    ),
    (
        "haoai",
        {"status": "done", "video_url": "https://video.example/haoai", "url": "ignored"},
        "https://custom.example/openai/v1/videos/external-123",
        30,
        False,
        {"Content-Type": "application/json"},
        "https://video.example/haoai",
    ),
    (
        "yiyun",
        {"status": "success", "video_url": "https://video.example/yiyun", "url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {"Content-Type": "application/json"},
        "https://video.example/yiyun",
    ),
    (
        "suqing",
        {"status": "succeeded", "video_url": "https://video.example/suqing", "url": "ignored"},
        "https://custom.example/v1/videos/external-123",
        30,
        False,
        {"Content-Type": "application/json"},
        "https://video.example/suqing",
    ),
]


@pytest.mark.parametrize(
    "provider,payload,expected_url,timeout,ipv4,extra_headers,expected_result",
    PROTOCOL_CASES,
)
def test_each_provider_uses_its_single_get_contract(
    provider: str,
    payload: dict,
    expected_url: str,
    timeout: float,
    ipv4: bool,
    extra_headers: dict[str, str],
    expected_result: str,
) -> None:
    session = FakeConfigurationSession(
        system_configuration=make_configuration(
            provider,
            base_url="https://custom.example/v1/",
            api_key="source-key",
        )
    )
    response = FakeResponse(status=200, payload=payload)
    http_factory = FakeHttpClientFactory(response)
    query = create_provider_status_service(make_runtime(session, http_factory)).prepare(provider)
    assert query is not None

    status, data = asyncio.run(query.poll_once("external-123"))
    result = query.extract_result(data)

    assert (status, result) == ("completed", expected_result)
    assert session.closed
    assert len(http_factory.requests) == 1
    request = http_factory.requests[0]
    assert request.url == expected_url
    assert request.headers == {
        "Authorization": "Bearer source-key",
        **extra_headers,
    }
    assert request.timeout_seconds == timeout
    assert len(http_factory.client_contexts) == 1
    assert http_factory.client_contexts[0].use_ipv4 is ipv4
    assert http_factory.client_contexts[0].closed
    assert http_factory.response_contexts[0].closed
    assert response.json_calls == 1


@pytest.mark.parametrize("provider", [case[0] for case in PROTOCOL_CASES])
def test_non_200_is_pending_without_reading_body_for_every_provider(provider: str) -> None:
    session = FakeConfigurationSession(
        system_configuration=make_configuration(
            provider,
            base_url="https://custom.example/v1/",
            api_key="key",
        )
    )
    response = FakeResponse(status=503, payload={"status": "completed"})
    http_factory = FakeHttpClientFactory(response)
    query = create_provider_status_service(make_runtime(session, http_factory)).prepare(provider)
    assert query is not None

    assert asyncio.run(query.poll_once("task-id")) == ("pending", {})

    assert response.json_calls == 0
    assert http_factory.client_contexts[0].closed
    assert http_factory.response_contexts[0].closed


STATUS_CASES = [
    ("zhangyuge", ("completed",), ("failed",), "COMPLETED"),
    ("manxiaobai", ("completed", "done", "SUCCESS", "succeeded"), ("failed", "FAILURE"), "success"),
    ("xai", ("done",), ("failed", "expired"), "DONE"),
    ("manxueapi", ("completed", "success", "succeeded"), ("failed", "error", "cancelled"), "SUCCESS"),
    ("geeknow", ("completed", "success", "succeeded"), ("failed", "failure", "error", "cancelled", "expired"), "PROCESSING"),
    ("snumom", ("completed",), ("failed",), "COMPLETE"),
    ("biglongxia", ("completed",), ("failed",), "COMPLETE"),
    ("heima", ("completed", "complete", "succeeded", "success", "done", "finished"), ("failed", "failure", "error", "cancelled", "canceled", "expired"), "IN_PROGRESS"),
    ("yu25", ("completed", "complete", "succeeded", "success", "done"), ("failed", "failure", "error", "cancelled", "canceled", "expired"), "PROCESSING"),
    ("yu25_beiyong", ("completed", "complete", "succeeded", "success", "done", "finished", "ready"), ("failed", "failure", "error", "cancelled", "canceled", "rejected", "expired"), "PROCESSING"),
    ("haoai", ("completed", "done"), ("failed",), "COMPLETED"),
    ("yiyun", ("completed", "success", "succeeded"), ("failed", "error", "cancelled"), "Success"),
    ("suqing", ("completed", "success", "succeeded"), ("failed", "error", "cancelled"), "processing"),
]


def status_payload(provider: str, status: str) -> dict:
    if provider == "heima":
        return {"data": {"status": status}}
    if provider == "yu25_beiyong":
        return {"data": {"data": {"status": status}}}
    return {"status": status}


def test_each_provider_preserves_accepted_status_spelling() -> None:
    for provider, completed, failed, pending in STATUS_CASES:
        protocol = get_provider_protocol(provider)
        assert protocol is not None
        for raw_status in completed:
            state, _ = protocol.parse_status(status_payload(provider, raw_status))
            assert state == "completed", (provider, raw_status)
        for raw_status in failed:
            state, _ = protocol.parse_status(status_payload(provider, raw_status))
            assert state == "failed", (provider, raw_status)
        state, _ = protocol.parse_status(status_payload(provider, pending))
        assert state == "pending", (provider, pending)


def test_xai_expired_keeps_legacy_string_error_payload() -> None:
    protocol = get_provider_protocol("xai")
    assert protocol is not None

    assert protocol.parse_status({"status": "expired"}) == (
        "failed",
        {"error": "请求已过期"},
    )


def test_complex_result_paths_keep_fallback_order_and_regex_behavior() -> None:
    manxue = get_provider_protocol("manxueapi")
    assert manxue is not None
    assert manxue.extract_result(
        {
            "video_url": "video-url",
            "output": {"url": "output-url"},
            "metadata": {"url": "metadata-url"},
            "result": {"url": "result-url"},
            "result_url": "result-url-field",
            "url": "https://top-level.example/video",
        },
        "",
    ) == "video-url"
    assert manxue.extract_result(
        {
            "output": [{"url": "output-url"}],
            "metadata": {"url": "metadata-url"},
            "result": {"url": "result-url"},
        },
        "",
    ) == "output-url"

    backup = get_provider_protocol("yu25_beiyong")
    assert backup is not None
    assert backup.extract_result(
        {"choices": [{"message": {"content": "render: https://cdn.example/video.mp4)."}}]},
        "content-fallback",
    ) == "https://cdn.example/video.mp4"
    assert backup.extract_result({}, "content-fallback") == "content-fallback"

    yiyun = get_provider_protocol("yiyun")
    assert yiyun is not None
    assert yiyun.extract_result({"urls": [{"url": "object-url"}]}, "") == "object-url"
    assert yiyun.extract_result({"urls": ["string-url"]}, "") == "string-url"
