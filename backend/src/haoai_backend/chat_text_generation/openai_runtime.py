"""OpenAI-compatible stream adapter for legacy chat text generation."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit

from haoai_backend.generation_providers.text.openai_codec import OpenAITextStreamCodec
from haoai_backend.storyboard_generation.openai_runtime import (
    AiohttpStoryboardTextTransport,
    StoryboardTextTransport,
)

from .ports import ChatTextGenerationRuntime, ChatTextRuntimeCall

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "gpt-3.5-turbo"
REQUEST_TIMEOUT_SECONDS = 600

_MISSING_CONFIGURATION = "模型执行配置不完整，请检查服务配置"
_INVALID_ENDPOINT = "模型服务地址配置无效"
_UNAVAILABLE = "AI 服务暂时不可用，请检查模型配置"
_INVALID_RESPONSE = "AI 服务返回了无效响应"
_EMPTY_RESPONSE = "AI 服务未返回有效正文"


class AddressResolver(Protocol):
    async def __call__(self, host: str, port: int) -> Sequence[str]: ...


class OpenAICompatibleChatTextRuntime(ChatTextGenerationRuntime):
    """Stream chat completions through the shared lazy text transport."""

    def __init__(
        self,
        transport: StoryboardTextTransport | None = None,
        *,
        address_resolver: AddressResolver | None = None,
    ) -> None:
        self._transport = (
            transport
            if transport is not None
            else AiohttpStoryboardTextTransport(
                timeout_seconds=REQUEST_TIMEOUT_SECONDS
            )
        )
        self._address_resolver = address_resolver or _resolve_addresses

    async def stream(
        self,
        call: ChatTextRuntimeCall,
    ) -> AsyncIterator[str | Mapping[str, object]]:
        if (
            not isinstance(call, ChatTextRuntimeCall)
            or not isinstance(call.enable_thinking, bool)
            or not isinstance(call.model.provider, str)
            or not isinstance(call.model.api_key, str)
            or not call.model.api_key.strip()
        ):
            yield {"type": "error", "message": _MISSING_CONFIGURATION}
            return

        try:
            endpoint, parsed = _completion_endpoint(
                call.model.provider,
                call.model.base_url,
            )
            await _validate_endpoint(parsed, self._address_resolver)
        except asyncio.CancelledError:
            raise
        except Exception:
            yield {"type": "error", "message": _INVALID_ENDPOINT}
            return

        try:
            body = _request_body(call)
        except Exception:
            yield {"type": "error", "message": _MISSING_CONFIGURATION}
            return

        headers = {
            "Authorization": f"Bearer {call.model.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            async with self._transport.open_stream(
                endpoint,
                headers=headers,
                json_body=body,
            ) as response:
                if response.status != 200:
                    yield {"type": "error", "message": _UNAVAILABLE}
                    return

                codec = OpenAITextStreamCodec(status_code=response.status)
                async for chunk in response.iter_bytes():
                    for event in codec.feed(chunk):
                        if event.kind == "content" and event.text:
                            yield event.text
                        elif event.kind in {"upstream_error", "error"}:
                            yield {"type": "error", "message": _INVALID_RESPONSE}
                            return

                for event in codec.finalize():
                    if event.kind == "content" and event.text:
                        yield event.text
                    elif event.kind in {"upstream_error", "error"}:
                        yield {"type": "error", "message": _EMPTY_RESPONSE}
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            yield {"type": "error", "message": _UNAVAILABLE}


def _request_body(call: ChatTextRuntimeCall) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": call.model.model or DEFAULT_MODEL,
        "messages": [dict(message) for message in call.messages],
        "temperature": 0.7,
        "stream": True,
    }
    if call.enable_thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "high"
    else:
        body["thinking"] = {"type": "disabled"}
    return body


def _completion_endpoint(
    provider: str,
    base_url: str | None,
) -> tuple[str, SplitResult]:
    default_base_url = (
        DEEPSEEK_DEFAULT_BASE_URL
        if provider.strip().lower() == "deepseek"
        else OPENAI_DEFAULT_BASE_URL
    )
    base = base_url or default_base_url
    if not isinstance(base, str):
        raise ValueError("invalid endpoint")
    endpoint = base.rstrip("/") + "/chat/completions"
    parsed = urlsplit(endpoint)
    try:
        parsed.port
    except ValueError:
        raise ValueError("invalid endpoint") from None
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in endpoint)
        or "\\" in endpoint
    ):
        raise ValueError("invalid endpoint")
    return endpoint, parsed


async def _validate_endpoint(
    parsed: SplitResult,
    resolver: AddressResolver,
) -> None:
    if parsed.scheme == "https":
        return
    host = parsed.hostname
    if host is None:
        raise ValueError("invalid endpoint")
    if _is_internal_host(host):
        return
    addresses = await resolver(host, parsed.port or 80)
    if not addresses or not any(_is_internal_address(address) for address in addresses):
        raise ValueError("public model services require HTTPS")


async def _resolve_addresses(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(str(result[4][0]) for result in results)


def _is_internal_host(host: str) -> bool:
    return host.lower() == "localhost" or _is_internal_address(host)


def _is_internal_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
    )


__all__ = (
    "AddressResolver",
    "OpenAICompatibleChatTextRuntime",
)
