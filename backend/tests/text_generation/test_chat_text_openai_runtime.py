from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from typing import Any

from haoai_backend.chat_text_generation.openai_runtime import (
    OpenAICompatibleChatTextRuntime,
)
from haoai_backend.chat_text_generation.ports import ChatTextModel, ChatTextRuntimeCall


class _Response:
    def __init__(self, status: int, chunks: tuple[bytes, ...]) -> None:
        self.status = status
        self.chunks = chunks

    async def iter_bytes(self):
        for chunk in self.chunks:
            yield chunk


class _Transport:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    @asynccontextmanager
    async def open_stream(self, url, *, headers, json_body):
        self.requests.append(
            {"url": url, "headers": dict(headers), "json_body": dict(json_body)}
        )
        yield self.response


def _call(
    provider: str,
    base_url: str | None,
    model: str | None,
    *,
    enable_thinking: bool = True,
) -> ChatTextRuntimeCall:
    return ChatTextRuntimeCall(
        model=ChatTextModel(
            id="text-model-id",
            name="Fixture model",
            provider=provider,
            api_key="synthetic-provider-key",
            model=model,
            base_url=base_url,
            cost_per_call=0,
            is_system=False,
        ),
        messages=(
            {"role": "system", "content": "system 原文"},
            {"role": "user", "content": "user 原文"},
        ),
        enable_thinking=enable_thinking,
    )


async def _collect(runtime, call):
    return [event async for event in runtime.stream(call)]


def test_stream_preserves_legacy_provider_routes_messages_and_thinking() -> None:
    response_bytes = json.dumps(
        {"choices": [{"message": {"content": "已生成"}}]},
        ensure_ascii=False,
    ).encode("utf-8")
    cases = (
        (
            "openai",
            None,
            "fixture-openai-model",
            True,
            "https://api.openai.com/v1/chat/completions",
            "fixture-openai-model",
        ),
        (
            "deepseek",
            None,
            None,
            False,
            "https://api.deepseek.com/chat/completions",
            "gpt-3.5-turbo",
        ),
        (
            "custom-compatible",
            "https://custom.example/api/",
            "fixture-custom-model",
            True,
            "https://custom.example/api/chat/completions",
            "fixture-custom-model",
        ),
    )

    for provider, base_url, model, thinking, expected_url, expected_model in cases:
        transport = _Transport(_Response(200, (response_bytes,)))
        runtime = OpenAICompatibleChatTextRuntime(transport=transport)
        events = asyncio.run(
            _collect(
                runtime,
                _call(provider, base_url, model, enable_thinking=thinking),
            )
        )

        assert events == ["已生成"]
        request = transport.requests[0]
        assert request["url"] == expected_url
        assert request["headers"] == {
            "Authorization": "Bearer synthetic-provider-key",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        assert request["json_body"] == {
            "model": expected_model,
            "messages": [
                {"role": "system", "content": "system 原文"},
                {"role": "user", "content": "user 原文"},
            ],
            "temperature": 0.7,
            "stream": True,
            "thinking": {"type": "enabled" if thinking else "disabled"},
            **({"reasoning_effort": "high"} if thinking else {}),
        }


def test_stream_keeps_reasoning_fallback_and_maps_errors_without_raw_details() -> None:
    reasoning_transport = _Transport(
        _Response(
            200,
            (
                b'data: {"choices":[{"delta":{"reasoning_content":"reasoning fallback"}}]}\n\n',
                b"data: [DONE]\n\n",
            ),
        )
    )
    reasoning_events = asyncio.run(
        _collect(
            OpenAICompatibleChatTextRuntime(transport=reasoning_transport),
            _call("openai", None, "fixture-model"),
        )
    )
    assert reasoning_events == ["reasoning fallback"]

    failures = (
        (
            503,
            (b"synthetic upstream body with raw-secret",),
            "AI 服务暂时不可用，请检查模型配置",
        ),
        (
            200,
            (b'data: {"error":{"message":"provider private-detail raw-secret"}}\n\n',),
            "AI 服务返回了无效响应",
        ),
        (200, (), "AI 服务未返回有效正文"),
    )
    for status, chunks, expected_message in failures:
        transport = _Transport(_Response(status, chunks))
        events = asyncio.run(
            _collect(
                OpenAICompatibleChatTextRuntime(transport=transport),
                _call("openai", "https://provider.example/v1", "fixture-model"),
            )
        )
        assert events == [{"type": "error", "message": expected_message}]
        assert "raw-secret" not in repr(events)
        assert "private-detail" not in repr(events)
        assert "provider.example" not in repr(events)

    async def public_resolver(_host: str, _port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)

    transport = _Transport(_Response(200, (b'data: {"choices":[]}\n\n',)))
    runtime = OpenAICompatibleChatTextRuntime(
        transport=transport,
        address_resolver=public_resolver,
    )
    events = asyncio.run(
        _collect(
            runtime,
            _call("custom-compatible", "http://public.example/api", "fixture-model"),
        )
    )

    assert events == [{"type": "error", "message": "模型服务地址配置无效"}]
    assert transport.requests == []
