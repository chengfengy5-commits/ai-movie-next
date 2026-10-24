from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest

from haoai_backend.generation_providers.text.openai_codec import OpenAITextStreamCodec
from haoai_backend.storyboard_generation.domain import StoryboardServerModel
from haoai_backend.storyboard_generation.openai_runtime import AiohttpStoryboardTextTransport
from haoai_backend.shared.identity import TrustedActor
from haoai_backend.storyboard_asset_generation.application import PreparedAssetGeneration
from haoai_backend.storyboard_asset_generation.execution import stream_asset_events
from haoai_backend.storyboard_asset_generation.openai_runtime import (
    OpenAICompatibleStoryboardAssetRuntime,
    TRUNCATION_WARNING,
    _max_tokens,
)
from haoai_backend.storyboard_asset_generation.ports import StoryboardAssetRuntimeCall


class _Response:
    def __init__(self, chunks: Sequence[bytes], *, status: int = 200) -> None:
        self.status = status
        self.chunks = tuple(chunks)
        self.closed = False

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class _Transport:
    def __init__(self, responses: Sequence[_Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict[str, Any]] = []
        self.open_count = 0

    @asynccontextmanager
    async def open_stream(self, url, *, headers, json_body):
        self.open_count += 1
        self.requests.append({"url": url, "headers": dict(headers), "json_body": dict(json_body)})
        response = next(self.responses)
        try:
            yield response
        finally:
            response.closed = True


class _FakeAiohttpContent:
    def __init__(self, chunks: Sequence[bytes], waiting: asyncio.Event | None = None) -> None:
        self._chunks = tuple(chunks)
        self.waiting = waiting

    async def iter_chunked(self, _chunk_size: int):
        for chunk in self._chunks:
            yield chunk
        if self.waiting is not None:
            self.waiting.set()
            await asyncio.Future()


class _FakeAiohttpResponse:
    status = 200

    def __init__(self, chunks: Sequence[bytes], waiting: asyncio.Event | None = None) -> None:
        self.content = _FakeAiohttpContent(chunks, waiting)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        self.closed = True


class _FakeAiohttpSession:
    def __init__(self, timeout: Any, response: _FakeAiohttpResponse) -> None:
        self.timeout = timeout
        self.response = response
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        self.closed = True

    def post(self, *_args: Any, **_kwargs: Any) -> _FakeAiohttpResponse:
        return self.response


class _FakeClientTimeout:
    def __init__(self, total: float) -> None:
        self.total = total


class _FakeAiohttpBoundary:
    def __init__(self, responses: Sequence[_FakeAiohttpResponse]) -> None:
        self._responses = iter(responses)
        self.timeout_totals: list[float] = []
        self.sessions: list[_FakeAiohttpSession] = []

    def ClientTimeout(self, *, total: float):
        self.timeout_totals.append(total)
        return _FakeClientTimeout(total)

    def ClientSession(self, *, timeout: Any) -> _FakeAiohttpSession:
        session = _FakeAiohttpSession(timeout, next(self._responses))
        self.sessions.append(session)
        return session


def _sse(*payloads: MappingLike) -> tuple[bytes, ...]:
    body = b"".join(
        b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        for payload in payloads
    )
    return tuple(body[offset:offset + 3] for offset in range(0, len(body), 3))


MappingLike = dict[str, Any]


def _call(
    *,
    provider: str = "openai",
    api_key: str = "synthetic-api-key",
    model: str | None = "test-model",
    base_url: str | None = "https://provider.example/v2",
    params: Any = None,
    enable_thinking: bool = False,
    system_prompt: str = "system",
    user_prompt: str = "user",
    raw_content: str = "script",
) -> StoryboardAssetRuntimeCall:
    return StoryboardAssetRuntimeCall(
        model=StoryboardServerModel(
            id="model-id",
            name="Model",
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            params=params,
        ),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        raw_content=raw_content,
        enable_thinking=enable_thinking,
    )


def _prepared(call: StoryboardAssetRuntimeCall) -> PreparedAssetGeneration:
    return PreparedAssetGeneration(
        actor=TrustedActor("actor"),
        kind="character",
        action="extract",
        model=call.model,
        system_prompt=call.system_prompt,
        user_prompt=call.user_prompt,
        raw_content=call.raw_content,
        enable_thinking=call.enable_thinking,
    )


def _collect(runtime: OpenAICompatibleStoryboardAssetRuntime, call: StoryboardAssetRuntimeCall):
    async def run() -> list[Any]:
        return [item async for item in runtime.stream(call)]

    return asyncio.run(run())


def _collect_events(runtime: OpenAICompatibleStoryboardAssetRuntime, call: StoryboardAssetRuntimeCall):
    async def run() -> list[dict[str, Any]]:
        events = []
        async for line in stream_asset_events(runtime, _prepared(call)):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("provider", "thinking", "expected"),
    [
        ("openai", True, {"type": "enabled"}),
        ("deepseek", True, {"type": "enabled"}),
        ("openai", False, {"type": "disabled"}),
        ("deepseek", False, {"type": "disabled"}),
    ],
)
def test_native_provider_thinking_and_legacy_request_shape(
    provider: str,
    thinking: bool,
    expected: dict[str, str],
) -> None:
    payload = {"choices": [{"delta": {"content": "[]"}, "finish_reason": "stop"}]}
    transport = _Transport([_Response(_sse(payload))])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    _collect(runtime, _call(provider=provider, enable_thinking=thinking, params='{"max_tokens":{"default":1536}}'))

    request = transport.requests[0]
    assert request["url"] == "https://provider.example/v2/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer synthetic-api-key"
    assert request["json_body"] == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user\n\nscript"},
        ],
        "temperature": 0.7,
        "max_tokens": 1536,
        "stream": True,
        "thinking": expected,
        **({"reasoning_effort": "high"} if thinking else {}),
    }


def test_custom_provider_disables_thinking_and_preserves_empty_raw_content_separator() -> None:
    transport = _Transport([_Response(_sse({"choices": [{"delta": {"content": "[]"}}]}))])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    _collect(
        runtime,
        _call(
            provider="custom-provider",
            enable_thinking=True,
            model=" ",
            base_url=None,
            params={"max_tokens": {"value": "2048"}},
            raw_content="",
        ),
    )

    request = transport.requests[0]
    assert request["url"] == "https://api.openai.com/v1/chat/completions"
    assert request["json_body"]["model"] == "gpt-3.5-turbo"
    assert request["json_body"]["messages"][1]["content"] == "user\n\n"
    assert request["json_body"]["max_tokens"] == 2048
    assert request["json_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in request["json_body"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ('{"max_tokens":128}', 128),
        ('{"max_tokens":{"default":256}}', 256),
        ('{"max_tokens":{"value":512}}', 512),
        ({"max_tokens": 1024}, 1024),
        ('{"max_tokens":0}', 8192),
        ('{"max_tokens":true}', 1),
        ('{"max_tokens":"bad"}', 8192),
        ("not-json", 8192),
        (None, 8192),
    ],
)
def test_max_tokens_uses_positive_values_or_legacy_default(params: Any, expected: int) -> None:
    assert _max_tokens(params) == expected


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"max_tokens": {"default": "", "value": 1024}}, 1024),
        ({"max_tokens": {"default": None, "value": 1024}}, 1024),
        ({"max_tokens": {"default": 0, "value": 2048}}, 2048),
        ({"max_tokens": {"default": True, "value": 1024}}, 1),
        ({"max_tokens": True}, 1),
        ({"max_tokens": False}, 8192),
    ],
)
def test_request_body_preserves_nested_max_tokens_selection_and_integer_conversion(
    params: Any,
    expected: int,
) -> None:
    transport = _Transport([_Response(_sse({"choices": [{"delta": {"content": "[]"}}]}))])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    _collect(runtime, _call(params=params))

    assert transport.requests[0]["json_body"]["max_tokens"] == expected


def test_anthropic_returns_legacy_unsupported_error_without_opening_transport() -> None:
    transport = _Transport([])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    result = _collect(runtime, _call(provider="anthropic"))

    assert result == [{"type": "error", "message": "Anthropic流式输出暂不支持，请使用非流式模式"}]
    assert transport.open_count == 0


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?token=secret",
        "https://provider.example/v1#fragment",
        "ftp://provider.example/v1",
        "http://provider.example/v1",
    ],
)
def test_unsafe_or_unsupported_urls_fail_before_transport(base_url: str) -> None:
    transport = _Transport([])

    async def public_addresses(host: str, port: int) -> Sequence[str]:
        assert (host, port) == ("provider.example", 80)
        return ("8.8.8.8",)

    runtime = OpenAICompatibleStoryboardAssetRuntime(
        transport=transport,
        address_resolver=public_addresses,
    )
    result = _collect(runtime, _call(base_url=base_url))

    assert result == [{"type": "error", "message": "模型服务地址配置无效"}]
    assert transport.open_count == 0


def test_http_private_dns_and_internal_literals_are_allowed_with_fake_resolution() -> None:
    transport = _Transport(
        [
            _Response(_sse({"choices": [{"delta": {"content": "[]"}}]}))
            for _ in range(7)
        ]
    )
    resolutions: list[tuple[str, int]] = []

    async def private_addresses(host: str, port: int) -> Sequence[str]:
        resolutions.append((host, port))
        return ("10.1.2.3",)

    runtime = OpenAICompatibleStoryboardAssetRuntime(
        transport=transport,
        address_resolver=private_addresses,
    )
    _collect(runtime, _call(base_url="http://internal.example:8080/v2"))
    for base_url in (
        "http://localhost:8081/v2",
        "http://127.0.0.1:8081/v2",
        "http://10.1.2.3:8081/v2",
        "http://[::1]:8081/v2",
        "http://0.0.0.0:8081/v2",
        "http://169.254.1.2:8081/v2",
    ):
        _collect(runtime, _call(base_url=base_url))

    assert resolutions == [("internal.example", 8080)]
    assert [request["url"] for request in transport.requests] == [
        "http://internal.example:8080/v2/chat/completions",
        "http://localhost:8081/v2/chat/completions",
        "http://127.0.0.1:8081/v2/chat/completions",
        "http://10.1.2.3:8081/v2/chat/completions",
        "http://[::1]:8081/v2/chat/completions",
        "http://0.0.0.0:8081/v2/chat/completions",
        "http://169.254.1.2:8081/v2/chat/completions",
    ]


def test_http_domain_allows_mixed_private_and_public_dns_answers() -> None:
    transport = _Transport([_Response(_sse({"choices": [{"delta": {"content": "[]"}}]}))])

    async def mixed_addresses(host: str, port: int) -> Sequence[str]:
        assert (host, port) == ("mixed.example", 80)
        return ("10.1.2.3", "8.8.8.8")

    runtime = OpenAICompatibleStoryboardAssetRuntime(
        transport=transport,
        address_resolver=mixed_addresses,
    )

    result = _collect(runtime, _call(base_url="http://mixed.example/v1"))

    assert result == ["[]"]
    assert transport.requests[0]["url"] == "http://mixed.example/v1/chat/completions"


@pytest.mark.parametrize(
    ("status", "chunks", "expected_message"),
    [
        (200, (), "AI 服务未返回有效正文"),
        (200, (b"<!doctype html><html>proxy error</html>",), "AI 服务未返回有效正文"),
        (200, _sse({"error": {"message": "provider rejected secret"}}), "AI 服务返回了无效响应"),
        (503, (b"unavailable",), "AI 服务未返回有效正文"),
    ],
)
def test_empty_html_and_provider_errors_never_complete(
    status: int,
    chunks: Sequence[bytes],
    expected_message: str,
) -> None:
    response = _Response(chunks, status=status)
    transport = _Transport([response])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    events = _collect_events(runtime, _call())

    assert events[-1] == {"type": "error", "message": expected_message}
    assert not any(event["type"] in {"complete", "done"} for event in events)
    assert response.closed is True


def test_reasoning_only_falls_back_to_body_and_length_finish_emits_warning() -> None:
    response = _Response(
        _sse(
            {"choices": [{"delta": {"reasoning_content": "["}}]},
            {"choices": [{"delta": {"reasoning_content": "]"}, "finish_reason": "length"}]},
        )
    )
    transport = _Transport([response])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)

    events = _collect_events(runtime, _call())

    complete = next(event for event in events if event["type"] == "complete")
    assert complete["raw_response"] == "[]"
    assert {"type": "warning", "message": TRUNCATION_WARNING} in events
    assert events[-1] == {"type": "done"}


def test_non_first_choice_content_and_length_reason_are_ignored_by_asset_runtime() -> None:
    response = _Response(
        _sse(
            {
                "choices": [
                    {"delta": {"content": "[]"}, "finish_reason": "stop"},
                    {"delta": {"content": "ignored"}, "finish_reason": "length"},
                ]
            }
        )
    )
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=_Transport([response]))

    events = _collect_events(runtime, _call())

    complete = next(event for event in events if event["type"] == "complete")
    assert complete["raw_response"] == "[]"
    assert not any(event["type"] == "warning" for event in events)


def test_upstream_error_after_partial_content_does_not_emit_complete_or_done() -> None:
    response = _Response(
        _sse(
            {"choices": [{"delta": {"content": "["}}]},
            {"error": {"message": "provider failure"}},
        )
    )
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=_Transport([response]))

    events = _collect_events(runtime, _call())

    assert any(event["type"] == "content" for event in events)
    assert events[-1] == {"type": "error", "message": "AI 服务返回了无效响应"}
    assert not any(event["type"] in {"complete", "done"} for event in events)


def test_codec_exposes_first_choice_finish_reason_without_changing_event_output() -> None:
    codec = OpenAITextStreamCodec(first_choice_only=True)
    assert codec.finish_reason is None
    assert codec.feed(
        b'data: {"choices":[{"delta":{"content":"[]"},"finish_reason":"length"},'
        b'{"delta":{"content":"ignored"},"finish_reason":"stop"}]}\n'
    )[0].text == "[]"

    assert codec.finish_reason == "length"


def test_closing_asset_request_closes_fake_http_response_without_terminal_events() -> None:
    response = _Response(_sse({"choices": [{"delta": {"content": "[]"}}]}))
    transport = _Transport([response])
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=transport)
    call = _call()

    async def close_after_content() -> list[dict[str, Any]]:
        stream = stream_asset_events(runtime, _prepared(call))
        seen = []
        async for line in stream:
            if line.startswith("data: "):
                event = json.loads(line[6:])
                seen.append(event)
                if event.get("type") == "content":
                    await stream.aclose()
                    break
        return seen

    emitted = asyncio.run(close_after_content())
    assert emitted[-1]["type"] == "content"
    assert not any(event["type"] in {"complete", "done", "error"} for event in emitted)
    assert response.closed is True


def test_credential_redaction_spans_provider_content_events() -> None:
    response = _Response(
        _sse(
            {"choices": [{"delta": {"content": "[]synthetic-"}}]},
            {"choices": [{"delta": {"content": "api-key[]"}, "finish_reason": "stop"}]},
        )
    )
    runtime = OpenAICompatibleStoryboardAssetRuntime(transport=_Transport([response]))

    events = _collect_events(runtime, _call(api_key="synthetic-api-key"))
    serialized = json.dumps(events, ensure_ascii=False)

    assert "synthetic-api-key" not in serialized
    assert "[已隐藏]" in serialized
    assert events[-1] == {"type": "done"}


def test_aiohttp_transport_passes_default_and_asset_timeouts_to_client_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = b'data: {"choices":[{"delta":{"content":"[]"}}]}\n'
    responses = [_FakeAiohttpResponse([wire]), _FakeAiohttpResponse([wire])]
    boundary = _FakeAiohttpBoundary(responses)
    monkeypatch.setitem(sys.modules, "aiohttp", boundary)

    async def read_default_transport() -> list[bytes]:
        transport = AiohttpStoryboardTextTransport()
        async with transport.open_stream(
            "https://provider.example.test/v1/chat/completions",
            headers={},
            json_body={},
        ) as response:
            return [chunk async for chunk in response.iter_bytes()]

    assert asyncio.run(read_default_transport()) == [wire]

    runtime = OpenAICompatibleStoryboardAssetRuntime()
    assert _collect(runtime, _call()) == ["[]"]

    assert boundary.timeout_totals == [300, 600]
    assert [session.timeout.total for session in boundary.sessions] == [300, 600]
    assert all(session.closed for session in boundary.sessions)
    assert all(response.closed for response in responses)


def test_aiohttp_asset_cancellation_closes_response_and_session_without_terminal_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = asyncio.Event()
    wire = b'data: {"choices":[{"delta":{"content":"[]"}}]}\n'
    response = _FakeAiohttpResponse([wire], waiting)
    boundary = _FakeAiohttpBoundary([response])
    monkeypatch.setitem(sys.modules, "aiohttp", boundary)
    runtime = OpenAICompatibleStoryboardAssetRuntime()
    call = _call()

    async def cancel_after_content() -> list[dict[str, Any]]:
        stream = stream_asset_events(runtime, _prepared(call))
        seen: list[dict[str, Any]] = []
        while True:
            event = json.loads((await anext(stream))[6:])
            seen.append(event)
            if event.get("type") == "content":
                break

        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        await stream.aclose()
        return seen

    emitted = asyncio.run(cancel_after_content())

    assert any(event["type"] == "content" for event in emitted)
    assert not any(event["type"] in {"complete", "done", "error"} for event in emitted)
    assert response.closed is True
    assert boundary.sessions[0].closed is True
