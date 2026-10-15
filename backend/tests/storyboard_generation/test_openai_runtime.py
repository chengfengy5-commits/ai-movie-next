from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from haoai_backend.storyboard_generation.domain import StoryboardRuntimeCall, StoryboardServerModel
from haoai_backend.storyboard_generation.errors import (
    StoryboardGenerationUnavailable,
    StoryboardProviderFailure,
)
from haoai_backend.storyboard_generation.openai_runtime import (
    OpenAICompatibleStoryboardRuntime,
)


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status = status
        self._chunks = chunks

    async def iter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeTransport:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    @asynccontextmanager
    async def open_stream(self, url, *, headers, json_body):
        self.requests.append({"url": url, "headers": dict(headers), "body": dict(json_body)})
        yield self.response


def _call(*, provider: str = "openai-compatible") -> StoryboardRuntimeCall:
    return StoryboardRuntimeCall(
        model=StoryboardServerModel(
            id="model-id",
            name="Synthetic model",
            provider=provider,
            api_key="private-test-key",
            model="wire-model",
            base_url="https://provider.example.test/v1/",
        ),
        system_prompt="system instructions",
        user_prompt="user instructions",
        raw_content="raw source",
        temperature=0.4,
        enable_thinking=True,
        operation="scene_segmentation",
    )


def test_runtime_uses_injected_transport_and_incremental_utf8_codec() -> None:
    event = {"choices": [{"delta": {"content": "跨域正文"}}]}
    line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
    split = line.index("跨".encode("utf-8")) + 1
    response = FakeResponse(200, [line[:split], line[split:], b"data: [DONE]\n\n"])
    transport = FakeTransport(response)
    runtime = OpenAICompatibleStoryboardRuntime(transport)

    async def collect() -> list[str]:
        return [chunk async for chunk in runtime.stream(_call())]

    chunks = asyncio.run(collect())

    assert chunks == ["跨域正文"]
    request = transport.requests[0]
    assert request["url"] == "https://provider.example.test/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer private-test-key"
    assert request["body"]["stream"] is True
    assert request["body"]["messages"] == [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "user instructions\n\nraw source"},
    ]
    assert request["body"]["reasoning_effort"] == "high"


def test_runtime_rejects_unknown_provider_before_opening_transport() -> None:
    transport = FakeTransport(FakeResponse(200, []))
    runtime = OpenAICompatibleStoryboardRuntime(transport)

    async def collect() -> None:
        async for _chunk in runtime.stream(_call(provider="unknown")):
            pass

    with pytest.raises(StoryboardGenerationUnavailable):
        asyncio.run(collect())

    assert transport.requests == []


def test_runtime_hides_provider_credentials_and_url_on_http_failure() -> None:
    transport = FakeTransport(FakeResponse(503, [b"private upstream response"]))
    runtime = OpenAICompatibleStoryboardRuntime(transport)

    async def collect() -> None:
        async for _chunk in runtime.stream(_call()):
            pass

    with pytest.raises(StoryboardProviderFailure) as raised:
        asyncio.run(collect())

    detail = str(raised.value)
    assert "private-test-key" not in detail
    assert "provider.example.test" not in detail
    assert "private upstream response" not in detail
