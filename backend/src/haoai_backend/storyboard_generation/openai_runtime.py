"""Explicitly injected OpenAI-compatible text transport for storyboard calls."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from haoai_backend.generation_providers.text.openai_codec import OpenAITextStreamCodec

from .domain import StoryboardRuntimeCall, StoryboardRuntimeResult
from .errors import StoryboardGenerationUnavailable, StoryboardProviderFailure


class StoryboardHttpResponse(Protocol):
    status: int

    def iter_bytes(self) -> AsyncIterator[bytes]: ...


class StoryboardTextTransport(Protocol):
    def open_stream(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AbstractAsyncContextManager[StoryboardHttpResponse]: ...


class OpenAICompatibleStoryboardRuntime:
    """Stream text from an explicitly configured OpenAI-compatible transport."""

    def __init__(self, transport: StoryboardTextTransport) -> None:
        self._transport = transport

    async def generate(self, call: StoryboardRuntimeCall) -> StoryboardRuntimeResult:
        chunks: list[str] = []
        async for chunk in self.stream(call):
            chunks.append(chunk)
        return StoryboardRuntimeResult("".join(chunks))

    async def stream(self, call: StoryboardRuntimeCall) -> AsyncIterator[str]:
        provider = call.model.provider.strip().lower()
        if provider not in {"openai", "openai-compatible"}:
            raise StoryboardGenerationUnavailable("当前模型暂不支持两步分镜生成")
        if not call.model.api_key or not call.model.api_key.strip():
            raise StoryboardGenerationUnavailable("模型执行配置不完整，请检查服务配置")
        if not call.model.model or not call.model.model.strip():
            raise StoryboardGenerationUnavailable("模型执行配置不完整，请检查服务配置")

        url = _completion_url(call.model.base_url)
        body = _request_body(call)
        headers = {
            "Authorization": f"Bearer {call.model.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        async with self._transport.open_stream(url, headers=headers, json_body=body) as response:
            if response.status != 200:
                raise StoryboardProviderFailure("AI 服务暂时不可用，请检查模型配置")
            codec = OpenAITextStreamCodec(status_code=response.status)
            async for chunk in response.iter_bytes():
                for event in codec.feed(chunk):
                    if event.kind == "content" and event.text:
                        yield event.text
                    elif event.kind in {"upstream_error", "error"}:
                        raise StoryboardProviderFailure("AI 服务返回了无效响应")
            for event in codec.finalize():
                if event.kind == "content" and event.text:
                    yield event.text
                elif event.kind in {"upstream_error", "error"}:
                    raise StoryboardProviderFailure("AI 服务未返回有效正文")


class AiohttpStoryboardTextTransport:
    """Lazy aiohttp transport; import and network access happen only per call."""

    def __init__(self, *, timeout_seconds: float = 300) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    @asynccontextmanager
    async def open_stream(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ):
        try:
            import aiohttp
        except ImportError as error:
            raise StoryboardGenerationUnavailable("AI 文本传输依赖尚未安装") from error

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=dict(headers), json=dict(json_body)) as response:
                yield _AiohttpStoryboardResponse(response)


class _AiohttpStoryboardResponse:
    def __init__(self, response: Any) -> None:
        self.status = response.status
        self._response = response

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.content.iter_chunked(4096):
            yield chunk


def _request_body(call: StoryboardRuntimeCall) -> dict[str, Any]:
    user_text = call.user_prompt
    if call.raw_content:
        user_text += "\n\n" + call.raw_content
    payload: dict[str, Any] = {
        "model": call.model.model,
        "messages": [
            {"role": "system", "content": call.system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": call.temperature,
        "stream": True,
    }
    if call.enable_thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "high"
    elif call.operation == "scene_segmentation":
        payload["thinking"] = {"type": "disabled"}
    return payload


def _completion_url(base_url: str | None) -> str:
    value = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StoryboardGenerationUnavailable("模型服务地址配置无效")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if path.endswith("/v1"):
        path += "/chat/completions"
    else:
        path += "/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


__all__ = (
    "AiohttpStoryboardTextTransport",
    "OpenAICompatibleStoryboardRuntime",
    "StoryboardHttpResponse",
    "StoryboardTextTransport",
)
